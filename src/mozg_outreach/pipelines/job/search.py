"""Job search phases. A run manager calls them in order, with the strategy gate in between."""

import asyncio
import json
import logging
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field, TypeAdapter

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.domain import FieldValue, ItemCandidate, ItemKind, ItemScore
from mozg_outreach.pipelines.job.listings import SourcedListing
from mozg_outreach.pipelines.job.models import (
    ContactInput,
    JobBrief,
    JobContacts,
    JobScoutInput,
    JobScoutResult,
    JobSource,
    JobStrategy,
    JobStrategyInput,
    JudgeInput,
    JudgeResult,
    Listing,
    ListingJudgment,
    SourcePlan,
    SourceResearchInput,
)
from mozg_outreach.pipelines.job.screening import (
    ListingScreener,
    Screening,
    ScreenRules,
    listing_text,
)
from mozg_outreach.pipelines.job.skills import SkillMatcher
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.scoring.job import JobScoreInput, JobScorer

logger = logging.getLogger(__name__)

JUDGE_BATCH = 10
MIN_PER_SOURCE, MAX_PER_SOURCE = 15, 50


class ScoutOutcome(BaseModel):
    listings: list[SourcedListing]
    empty_sources: list[str] = Field(description="Sources whose smoke query also found nothing.")
    concerns: list[str]


class RankedListing(BaseModel):
    listing: Listing
    score: ItemScore
    judgment: ListingJudgment | None


class Judgments(BaseModel):
    by_key: dict[str, ListingJudgment]
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
        strategy = await self._steps.run("job", "job-strategist", payload, JobStrategy)
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
            plan = await self._steps.run("job", "job-source-researcher", payload, SourcePlan)
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
            *(self._steps.run("job", "job-scout", p, JobScoutResult) for p in payloads),
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
                    SourcedListing(source=source.name, listing=item) for item in result.listings
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
        batches = [listings[i : i + JUDGE_BATCH] for i in range(0, len(listings), JUDGE_BATCH)]
        terms = [*strategy.skills(), *strategy.nice_to_have_skills]
        results = await asyncio.gather(
            *(
                self._steps.run(
                    "job",
                    "job-listing-judge",
                    JudgeInput(
                        cv=strategy.cv,
                        deal_breakers=strategy.preferences.deal_breakers,
                        listings=[listing.for_agents(terms) for listing in batch],
                    ),
                    JudgeResult,
                )
                for batch in batches
            ),
            return_exceptions=True,
        )
        judgments = Judgments(by_key={}, concerns=[])
        for batch, result in zip(batches, results, strict=True):
            if isinstance(result, AgentRunError | AgentOutputError):
                logger.warning("listing judge failed for a batch of %d: %s", len(batch), result)
                judgments.concerns.append(f"Judge failed for {len(batch)} listings: {result}")
                continue
            if isinstance(result, BaseException):
                raise result
            wanted = {listing.key for listing in batch}
            judgments.by_key |= {j.key: j for j in result.judgments if j.key in wanted}
            judgments.concerns += result.concerns
        missing = [listing.title for listing in listings if listing.key not in judgments.by_key]
        if missing:
            judgments.concerns.append(f"No judgment for: {', '.join(missing)}")
        return judgments

    def rank(
        self, listings: list[Listing], judgments: Judgments, strategy: JobStrategy, today: date
    ) -> list[RankedListing]:
        scorer, skills = JobScorer(), SkillMatcher(strategy)
        ranked = [
            RankedListing(
                listing=listing,
                judgment=judgments.by_key.get(listing.key),
                score=scorer.score(
                    JobScoreInput(
                        listing=listing,
                        skills=skills.match(listing_text(listing)),
                        judgment=judgments.by_key.get(listing.key),
                        strategy=strategy,
                        today=today,
                    )
                ),
            )
            for listing in listings
        ]
        return sorted(ranked, key=lambda r: r.score.total or 0.0, reverse=True)

    async def contacts(self, listing: Listing) -> JobContacts:
        return await self._steps.run(
            "job", "job-contact-finder", ContactInput(listing=listing.for_agents([])), JobContacts
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
            "salary": field(listing.salary.raw if listing.salary else None),
            "posted_on": field(listing.posted_on.isoformat() if listing.posted_on else None),
            "url": field(listing.url),
            "apply_url": field(listing.apply_url),
        },
    )


def load_canonical(path: Path) -> list[JobSource]:
    return TypeAdapter(list[JobSource]).validate_python(json.loads(path.read_text()))
