"""Freelance search phases, on the job pipeline's normalizer, gates and skill matcher."""

import asyncio
import json
import logging
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field, TypeAdapter

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.domain import FieldValue, ItemCandidate, ItemKind, ItemScore
from mozg_outreach.pipelines.freelance.budget import BudgetParser
from mozg_outreach.pipelines.freelance.models import (
    FreelanceBrief,
    FreelanceStrategy,
    FreelanceStrategyInput,
    Gig,
    GigJudgeInput,
    GigJudgeResult,
    GigJudgment,
    GigScoutInput,
    GigScoutResult,
    GigSourceResearchInput,
    GigTerms,
    ScoutedGig,
)
from mozg_outreach.pipelines.job.listings import SourcedListing, canonical_url
from mozg_outreach.pipelines.job.models import Excluded, JobSource, ScoutedListing, SourcePlan
from mozg_outreach.pipelines.job.screening import ListingScreener, ScreenRules, listing_text
from mozg_outreach.pipelines.job.skills import SkillMatcher
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.scoring.freelance import FreelanceScorer, GigScoreInput

logger = logging.getLogger(__name__)

JUDGE_BATCH = 10
MIN_PER_SOURCE, MAX_PER_SOURCE = 10, 40


class FreelanceCatalog(BaseModel):
    scrape: list[JobSource]
    guidance: list[JobSource] = Field(description="Gated networks: how to apply, never scouted.")


class SourcedGig(BaseModel):
    source: str
    gig: ScoutedGig


class GigScoutOutcome(BaseModel):
    gigs: list[SourcedGig]
    empty_sources: list[str]
    concerns: list[str]


class GigScreening(BaseModel):
    kept: list[Gig]
    excluded: list[Excluded]


class GigJudgments(BaseModel):
    by_key: dict[str, GigJudgment]
    concerns: list[str]


class RankedGig(BaseModel):
    gig: Gig
    score: ItemScore
    judgment: GigJudgment | None


class FreelanceSearch:
    def __init__(self, steps: PipelineSteps, catalog: FreelanceCatalog) -> None:
        self._steps = steps
        self._catalog = catalog
        self._budgets = BudgetParser()

    async def plan(self, brief: FreelanceBrief, feedback: str | None = None) -> FreelanceStrategy:
        payload = FreelanceStrategyInput(
            cv_text=brief.cv_text,
            preferences=brief.preferences,
            recency_days=brief.recency_days,
            feedback=feedback,
        )
        strategy = await self._steps.run("job", "freelance-strategist", payload, FreelanceStrategy)
        known = {s.casefold() for s in strategy.cv.skills}
        stray = [s for s in strategy.must_have_skills if s.casefold() not in known]
        if stray:
            note = f"Must-have skills not found in the CV skills list: {', '.join(stray)}"
            strategy.concerns.append(note)
        return strategy

    def guidance(self, strategy: FreelanceStrategy) -> list[JobSource]:
        return [s for s in self._catalog.guidance if _serves(s, strategy)]

    async def sources(self, strategy: FreelanceStrategy) -> list[JobSource]:
        canonical = [s for s in self._catalog.scrape if _serves(s, strategy)]
        payload = GigSourceResearchInput(strategy=strategy, canonical=canonical)
        try:
            plan = await self._steps.run("job", "job-source-researcher", payload, SourcePlan)
            augmented = plan.augmented
        except (AgentRunError, AgentOutputError) as exc:
            logger.warning("freelance source research failed, canonical only: %s", exc)
            augmented = []
        seen: set[str] = set()
        chosen: list[JobSource] = []
        for source in [*canonical, *augmented]:
            key = source.url.rstrip("/").lower()
            if source.scrape_allowed and key not in seen:
                seen.add(key)
                chosen.append(source)
        return chosen

    async def scout(
        self, strategy: FreelanceStrategy, sources: list[JobSource], brief: FreelanceBrief
    ) -> GigScoutOutcome:
        per_source = max(MIN_PER_SOURCE, min(MAX_PER_SOURCE, -(-brief.limit * 2 // len(sources))))
        titles = strategy.cv.top_titles
        payloads = [
            GigScoutInput(
                source=source,
                target_titles=titles,
                queries=[q for t in titles for q in strategy.search_queries.get(t, [])]
                + strategy.bilingual_query_variants.get(source.language_hint, []),
                title_aliases=[a for t in titles for a in strategy.title_aliases.get(t, [])],
                skills=strategy.cv.skills,
                discipline=strategy.discipline,
                engagement_types=strategy.preferences.engagement_types,
                region_hint=", ".join(strategy.preferences.regions_allowed),
                recency_days=brief.recency_days,
                max_results=per_source,
            )
            for source in sources
        ]
        results = await asyncio.gather(
            *(self._steps.run("job", "freelance-scout", p, GigScoutResult) for p in payloads),
            return_exceptions=True,
        )
        outcome = GigScoutOutcome(gigs=[], empty_sources=[], concerns=[])
        for source, result in zip(sources, results, strict=True):
            if isinstance(result, AgentRunError | AgentOutputError):
                logger.warning("gig scout for %s failed: %s", source.name, result)
                outcome.concerns.append(f"Scout for {source.name} failed: {result}")
            elif isinstance(result, BaseException):
                raise result
            else:
                outcome.gigs += [SourcedGig(source=source.name, gig=g) for g in result.gigs]
                if not result.gigs and result.zero_results_verified:
                    outcome.empty_sources.append(source.name)
                outcome.concerns += [f"{source.name}: {c}" for c in result.concerns]
        return outcome

    def screen(
        self,
        scouted: list[SourcedGig],
        strategy: FreelanceStrategy,
        brief: FreelanceBrief,
        today: date,
    ) -> GigScreening:
        terms = {canonical_url(s.gig.url): self._terms(s.gig) for s in scouted}
        screening = ListingScreener(gig_rules(strategy, brief), today).screen(
            [SourcedListing(source=s.source, listing=_as_listing(s.gig)) for s in scouted]
        )
        kept = [Gig(listing=listing, terms=terms[listing.key]) for listing in screening.kept]
        return GigScreening(kept=kept, excluded=screening.excluded)

    async def judge(self, gigs: list[Gig], strategy: FreelanceStrategy) -> GigJudgments:
        batches = [gigs[i : i + JUDGE_BATCH] for i in range(0, len(gigs), JUDGE_BATCH)]
        results = await asyncio.gather(
            *(
                self._steps.run(
                    "job",
                    "freelance-gig-judge",
                    GigJudgeInput(
                        cv=strategy.cv,
                        discipline=strategy.discipline,
                        deal_breakers=strategy.preferences.deal_breakers,
                        gigs=batch,
                    ),
                    GigJudgeResult,
                )
                for batch in batches
            ),
            return_exceptions=True,
        )
        judgments = GigJudgments(by_key={}, concerns=[])
        for batch, result in zip(batches, results, strict=True):
            if isinstance(result, AgentRunError | AgentOutputError):
                logger.warning("gig judge failed for a batch of %d: %s", len(batch), result)
                judgments.concerns.append(f"Judge failed for {len(batch)} gigs: {result}")
                continue
            if isinstance(result, BaseException):
                raise result
            wanted = {gig.listing.key for gig in batch}
            judgments.by_key |= {j.key: j for j in result.judgments if j.key in wanted}
            judgments.concerns += result.concerns
        missing = [g.listing.title for g in gigs if g.listing.key not in judgments.by_key]
        if missing:
            judgments.concerns.append(f"No judgment for: {', '.join(missing)}")
        return judgments

    def rank(
        self,
        gigs: list[Gig],
        judgments: GigJudgments,
        strategy: FreelanceStrategy,
        today: date,
    ) -> list[RankedGig]:
        scorer, skills = FreelanceScorer(), SkillMatcher(strategy)
        ranked = [
            RankedGig(
                gig=gig,
                judgment=judgments.by_key.get(gig.listing.key),
                score=scorer.score(
                    GigScoreInput(
                        gig=gig,
                        skills=skills.match(listing_text(gig.listing)),
                        judgment=judgments.by_key.get(gig.listing.key),
                        strategy=strategy,
                        today=today,
                    )
                ),
            )
            for gig in gigs
        ]
        return sorted(ranked, key=lambda r: r.score.total or 0.0, reverse=True)

    def _terms(self, gig: ScoutedGig) -> GigTerms:
        return GigTerms(
            budget=self._budgets.parse(gig.budget_raw, gig.duration_raw),
            duration_raw=gig.duration_raw,
            scope=gig.scope_raw,
            application_method=gig.application_method_raw,
        )


def gig_rules(strategy: FreelanceStrategy, brief: FreelanceBrief) -> ScreenRules:
    prefs = strategy.preferences
    return ScreenRules(
        cv=strategy.cv,
        places=prefs,
        work_auth_blockers=strategy.work_auth_blockers,
        skills=strategy,
        blocklist={
            "industry_blocklist": prefs.excluded_industries,
            "attribute_blocklist": prefs.excluded_companies,
        },
        recency_days=brief.recency_days,
    )


def to_candidate(gig: Gig) -> ItemCandidate:
    listing = gig.listing

    def field(value: str | None) -> FieldValue:
        return FieldValue(value=value, source_url=listing.url)

    return ItemCandidate(
        kind=ItemKind.GIG,
        dedup_key=listing.key,
        title=f"{listing.title} for {listing.company}"[:300],
        fields={
            "client": field(listing.company),
            "gig": field(listing.title),
            "location": field(listing.location_string),
            "budget": field(gig.terms.budget.raw if gig.terms.budget else None),
            "duration": field(gig.terms.duration_raw),
            "posted_on": field(listing.posted_on.isoformat() if listing.posted_on else None),
            "url": field(listing.url),
        },
    )


def load_catalog(path: Path) -> FreelanceCatalog:
    raw = json.loads(path.read_text())
    sources = TypeAdapter(list[JobSource])
    return FreelanceCatalog(
        scrape=sources.validate_python(raw["scrape_sources"]),
        guidance=sources.validate_python(raw["guidance_sources"]),
    )


def _serves(source: JobSource, strategy: FreelanceStrategy) -> bool:
    if not source.discipline:
        return True
    served = {d.strip() for d in source.discipline.split(",")}
    return bool(served & {strategy.discipline, *strategy.secondary_disciplines})


def _as_listing(gig: ScoutedGig) -> ScoutedListing:
    return ScoutedListing(
        title=gig.title,
        company=gig.client,
        location_string=gig.location_string,
        remote_status_raw=gig.remote_status_raw,
        url=gig.url,
        posted_date=gig.posted_date,
        salary_string=None,
        summary_snippet=gig.scope_raw,
    )
