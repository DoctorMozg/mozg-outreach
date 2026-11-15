"""Job search phases. A run manager calls them in order, with the strategy gate in between."""

import asyncio
import json
import logging
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field, TypeAdapter

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.domain import FieldValue, ItemCandidate, ItemKind
from mozg_outreach.pipelines.seeker.judge import JudgeContext, Judgments, ListingJudge
from mozg_outreach.pipelines.seeker.listings import SourcedListing
from mozg_outreach.pipelines.seeker.models import (
    ContactInput,
    JobBrief,
    JobContacts,
    JobScoutInput,
    JobScoutResult,
    JobSource,
    JobStrategy,
    JobStrategyInput,
    Listing,
    SourcePlan,
    SourceResearchInput,
)
from mozg_outreach.pipelines.seeker.ranking import RankedListing, Ranker
from mozg_outreach.pipelines.seeker.screening import ListingScreener, Screening, ScreenRules
from mozg_outreach.pipelines.seeker.skills import SkillMatcher
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.scoring.seeker import ScoreProfile

logger = logging.getLogger(__name__)

MIN_PER_SOURCE, MAX_PER_SOURCE = 15, 50


class ScoutOutcome(BaseModel):
    listings: list[SourcedListing]
    empty_sources: list[str] = Field(description="Sources whose smoke query also found nothing.")
    concerns: list[str]


class JobSearch:
    def __init__(self, steps: PipelineSteps, canonical: list[JobSource]) -> None:
        self._steps = steps
        self._canonical = canonical

    async def plan(self, brief: JobBrief, feedback: str | None = None) -> JobStrategy:
        payload = JobStrategyInput(
            cv_text=brief.cv_text,
            preferences=brief.preferences,
            recency_days=brief.recency_days,
            feedback=feedback,
        )
        strategy = await self._steps.run("seeker", "job-strategist", payload, JobStrategy)
        known = {s.casefold() for s in strategy.cv.skills}
        stray = [s for s in strategy.must_have_skills if s.casefold() not in known]
        if stray:
            # A must-have the CV doesn't show would gate out every listing it is missing from.
            note = f"Must-have skills not found in the CV skills list: {', '.join(stray)}"
            strategy.concerns.append(note)
        return strategy

    async def sources(self, strategy: JobStrategy) -> list[JobSource]:
        """Canonical boards first, then the agent's additions; only boards we may scout."""
        payload = SourceResearchInput(strategy=strategy, canonical=self._canonical)
        try:
            plan = await self._steps.run("seeker", "job-source-researcher", payload, SourcePlan)
            augmented = plan.augmented
        except (AgentRunError, AgentOutputError) as exc:
            logger.warning("source research failed, using canonical boards only: %s", exc)
            augmented = []
        seen: set[str] = set()
        chosen: list[JobSource] = []
        for source in [*self._canonical, *augmented]:
            key = source.url.rstrip("/").lower()
            if source.scrape_allowed and key not in seen:
                seen.add(key)
                chosen.append(source)
        return chosen

    async def scout(
        self, strategy: JobStrategy, sources: list[JobSource], brief: JobBrief
    ) -> ScoutOutcome:
        per_source = max(MIN_PER_SOURCE, min(MAX_PER_SOURCE, -(-brief.limit * 2 // len(sources))))
        titles = strategy.cv.top_titles
        payloads = [
            JobScoutInput(
                source=source,
                target_titles=titles,
                queries=[q for t in titles for q in strategy.search_queries.get(t, [])]
                + strategy.bilingual_query_variants.get(source.language_hint, []),
                title_aliases=[a for t in titles for a in strategy.title_aliases.get(t, [])],
                skills=strategy.cv.skills,
                region_hint=", ".join(strategy.preferences.regions_allowed),
                recency_days=brief.recency_days,
                max_results=per_source,
            )
            for source in sources
        ]
        results = await asyncio.gather(
            *(self._steps.run("seeker", "job-scout", p, JobScoutResult) for p in payloads),
            return_exceptions=True,
        )
        outcome = ScoutOutcome(listings=[], empty_sources=[], concerns=[])
        for source, result in zip(sources, results, strict=True):
            if isinstance(result, AgentRunError | AgentOutputError):
                logger.warning("scout for %s failed: %s", source.name, result)
                outcome.concerns.append(f"Scout for {source.name} failed: {result}")
            elif isinstance(result, BaseException):
                raise result
            else:
                outcome.listings += [
                    SourcedListing(source=source.name, kind="job", listing=item)
                    for item in result.listings
                ]
                if not result.listings and result.zero_results_verified:
                    outcome.empty_sources.append(source.name)
                outcome.concerns += [f"{source.name}: {c}" for c in result.concerns]
        return outcome

    def screen(
        self, scouted: list[SourcedListing], strategy: JobStrategy, brief: JobBrief, today: date
    ) -> Screening:
        return ListingScreener(ScreenRules.for_jobs(strategy, brief), today).screen(scouted)

    async def judge(self, listings: list[Listing], strategy: JobStrategy) -> Judgments:
        context = JudgeContext(
            cv=strategy.cv,
            discipline=None,
            deal_breakers=strategy.preferences.deal_breakers,
            terms=[*strategy.skills(), *strategy.nice_to_have_skills],
        )
        return await ListingJudge(self._steps).judge(listings, context)

    def rank(
        self, listings: list[Listing], judgments: Judgments, strategy: JobStrategy, today: date
    ) -> list[RankedListing]:
        profile = ScoreProfile(
            seniority=strategy.cv.seniority,
            weights=strategy.scoring_weights,
            pay_floor=strategy.preferences.salary_floor,
        )
        return Ranker(SkillMatcher(strategy), profile, today).rank(listings, judgments)

    async def contacts(self, listing: Listing) -> JobContacts:
        return await self._steps.run(
            "seeker",
            "job-contact-finder",
            ContactInput(listing=listing.for_agents([])),
            JobContacts,
        )


COLUMNS = ("company", "role", "location", "salary", "posted_on", "url", "apply_url")


def to_candidate(listing: Listing) -> ItemCandidate:
    def field(value: str | None) -> FieldValue:
        return FieldValue(value=value, source_url=listing.url)

    return ItemCandidate(
        kind=ItemKind.JOB,
        dedup_key=listing.key,
        title=(f"{listing.title} at {listing.company}" if listing.company else listing.title)[:300],
        fields={
            "company": field(listing.company),
            "role": field(listing.title),
            "location": field(listing.location_string),
            "salary": field(listing.pay.raw if listing.pay else None),
            "posted_on": field(listing.posted_on.isoformat() if listing.posted_on else None),
            "url": field(listing.url),
            "apply_url": field(listing.apply_url),
        },
    )


def load_canonical(path: Path) -> list[JobSource]:
    return TypeAdapter(list[JobSource]).validate_python(json.loads(path.read_text()))
