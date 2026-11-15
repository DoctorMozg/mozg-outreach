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
    JOB_ENGAGEMENTS,
    Board,
    BoardPlan,
    BoardResearchInput,
    ContactInput,
    EngagementType,
    Excluded,
    JobBrief,
    JobContacts,
    JobScoutInput,
    JobScoutResult,
    JobSource,
    JobStrategy,
    JobStrategyInput,
    KindPlan,
    Listing,
    ListingKind,
    ScoutInput,
    SeekerBrief,
    SeekerStrategy,
    SeekerStrategyInput,
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


KINDS: tuple[ListingKind, ...] = ("job", "gig")
LABELS: dict[ListingKind, str] = {"job": "salaried jobs", "gig": "freelance gigs"}
DEFAULT_ENGAGEMENTS: dict[ListingKind, list[EngagementType]] = {
    "job": ["full_time", "contract"],
    "gig": ["project_gig", "contractor_placement", "consulting_advisory"],
}
MIN_PER_BOARD, MAX_PER_BOARD = 10, 50


class BoardChoice(BaseModel):
    scouted: list[Board]
    guidance: list[Board] = Field(description="Gated networks: how to get in, never scouted.")


class KindScreening(BaseModel):
    kept: list[Listing]
    excluded: list[Excluded]
    over_limit: dict[ListingKind, int]


class SeekerSearch:
    """Jobs and gigs for one person: one strategy, one board list, one judge, scored per kind."""

    def __init__(self, steps: PipelineSteps, boards: list[Board]) -> None:
        self._steps = steps
        self._boards = boards

    async def plan(self, brief: SeekerBrief, feedback: str | None = None) -> SeekerStrategy:
        payload = SeekerStrategyInput(
            cv_text=brief.cv_text,
            preferences=brief.preferences,
            jobs=brief.jobs,
            gigs=brief.gigs,
            feedback=feedback,
        )
        strategy = await self._steps.run("seeker", "seeker-strategist", payload, SeekerStrategy)
        known = {s.casefold() for s in strategy.cv.skills}
        stray = [s for s in strategy.must_have_skills if s.casefold() not in known]
        if stray:
            # A must-have the CV doesn't show would gate out every listing it is missing from.
            note = f"Must-have skills not found in the CV skills list: {', '.join(stray)}"
            strategy.concerns.append(note)
        return _fit_to_brief(strategy, brief)

    async def boards(self, strategy: SeekerStrategy) -> BoardChoice:
        """Canonical boards first, then the agent's additions, kept to the kinds and disciplines."""
        canonical = [b for b in self._boards if _serves(b, strategy)]
        payload = BoardResearchInput(strategy=strategy, canonical=canonical)
        try:
            plan = await self._steps.run("seeker", "seeker-source-researcher", payload, BoardPlan)
            augmented = [b for b in plan.augmented if _serves(b, strategy)]
        except (AgentRunError, AgentOutputError) as exc:
            logger.warning("board research failed, using the catalog only: %s", exc)
            augmented = []
        choice = BoardChoice(scouted=[], guidance=[])
        seen: set[str] = set()
        for board in [*canonical, *augmented]:
            key = board.url.rstrip("/").lower()
            if key in seen:
                continue
            seen.add(key)
            if board.scrape_allowed:
                choice.scouted.append(board)
            elif board.type == "guidance":
                choice.guidance.append(board)
        return choice

    async def scout(
        self, strategy: SeekerStrategy, boards: list[Board], brief: SeekerBrief
    ) -> ScoutOutcome:
        """One scout per board and kind it serves, each told which kind it looks for."""
        payloads = _scout_inputs(strategy, boards, brief)
        results = await asyncio.gather(
            *(self._steps.run("seeker", "seeker-scout", p, JobScoutResult) for p in payloads),
            return_exceptions=True,
        )
        outcome = ScoutOutcome(listings=[], empty_sources=[], concerns=[])
        for payload, result in zip(payloads, results, strict=True):
            board, kind = payload.source, payload.kind
            if isinstance(result, AgentRunError | AgentOutputError):
                logger.warning("%s scout for %s failed: %s", kind, board.name, result)
                outcome.concerns.append(f"Scout for {board.name} ({kind}) failed: {result}")
            elif isinstance(result, BaseException):
                raise result
            else:
                outcome.listings += [
                    SourcedListing(source=board.name, kind=kind, listing=item)
                    for item in result.listings
                ]
                if not result.listings and result.zero_results_verified:
                    outcome.empty_sources.append(board.name)
                outcome.concerns += [f"{board.name}: {c}" for c in result.concerns]
        return outcome

    def screen(
        self,
        scouted: list[SourcedListing],
        strategy: SeekerStrategy,
        brief: SeekerBrief,
        today: date,
    ) -> KindScreening:
        """Each kind gated with its own recency window and cut to its own limit."""
        screening = KindScreening(kept=[], excluded=[], over_limit={})
        for kind in KINDS:
            scope = brief.scope(kind)
            if scope is None or strategy.plan_for(kind) is None:
                continue
            rules = _screen_rules(strategy, scope.recency_days)
            part = ListingScreener(rules, today).screen([s for s in scouted if s.kind == kind])
            screening.kept += part.kept[: scope.limit]
            screening.excluded += part.excluded
            screening.over_limit[kind] = max(0, len(part.kept) - scope.limit)
        return screening

    async def judge(self, listings: list[Listing], strategy: SeekerStrategy) -> Judgments:
        context = JudgeContext(
            cv=strategy.cv,
            discipline=strategy.discipline,
            deal_breakers=strategy.preferences.deal_breakers,
            terms=[*strategy.skills(), *strategy.nice_to_have_skills],
        )
        return await ListingJudge(self._steps).judge(listings, context)

    def rank(
        self, listings: list[Listing], judgments: Judgments, strategy: SeekerStrategy, today: date
    ) -> list[RankedListing]:
        """Each kind scored with its own weights and floor, then one list best first."""
        ranked: list[RankedListing] = []
        for kind in KINDS:
            plan = strategy.plan_for(kind)
            of_kind = [listing for listing in listings if listing.kind == kind]
            if plan is None or not of_kind:
                continue
            prefs = strategy.preferences
            profile = ScoreProfile(
                seniority=strategy.cv.seniority,
                weights=plan.scoring_weights,
                pay_floor=prefs.salary_floor if kind == "job" else prefs.rate_floor,
                engagement_types=prefs.engagement_types,
                discipline=strategy.discipline,
                secondary_disciplines=strategy.secondary_disciplines,
            )
            ranker = Ranker(SkillMatcher(strategy), profile, today)
            ranked += ranker.rank(of_kind, judgments)
        return sorted(ranked, key=lambda r: r.score.total or 0.0, reverse=True)


def _scout_inputs(
    strategy: SeekerStrategy, boards: list[Board], brief: SeekerBrief
) -> list[ScoutInput]:
    """A kind's limit, doubled for screening losses, is spread over the boards that serve it."""
    payloads: list[ScoutInput] = []
    prefs = strategy.preferences
    for kind in KINDS:
        plan, scope = strategy.plan_for(kind), brief.scope(kind)
        of_kind = [b for b in boards if kind in b.kinds]
        if plan is None or scope is None or not of_kind:
            continue
        per_board = max(MIN_PER_BOARD, min(MAX_PER_BOARD, -(-scope.limit * 2 // len(of_kind))))
        payloads += [
            ScoutInput(
                kind=kind,
                source=board,
                target_titles=plan.titles,
                queries=[q for t in plan.titles for q in plan.search_queries.get(t, [])]
                + plan.bilingual_query_variants.get(board.language_hint, []),
                title_aliases=[a for t in plan.titles for a in strategy.title_aliases.get(t, [])],
                skills=strategy.cv.skills,
                discipline=strategy.discipline,
                engagement_types=[e for e in prefs.engagement_types if _kind_of(e) == kind],
                region_hint=", ".join(prefs.regions_allowed),
                recency_days=scope.recency_days,
                max_results=per_board,
            )
            for board in of_kind
        ]
    return payloads


def _fit_to_brief(strategy: SeekerStrategy, brief: SeekerBrief) -> SeekerStrategy:
    """Plans and engagement types only for the kinds the brief asks for, never none for one."""
    concerns = list(strategy.concerns)
    plans: dict[str, KindPlan | None] = {}
    engagements = list(strategy.preferences.engagement_types)
    for kind in KINDS:
        wanted = brief.scope(kind) is not None
        plan = strategy.plan_for(kind) if wanted else None
        plans["jobs" if kind == "job" else "gigs"] = plan
        if wanted and plan is None:
            concerns.append(f"The strategy has no plan for {LABELS[kind]}, so none are searched.")
        if not wanted:
            engagements = [e for e in engagements if _kind_of(e) != kind]
        elif plan and not any(_kind_of(e) == kind for e in engagements):
            engagements += DEFAULT_ENGAGEMENTS[kind]
            concerns.append(f"No engagement type for {LABELS[kind]}; the defaults were used.")
    prefs = strategy.preferences.model_copy(update={"engagement_types": engagements})
    return strategy.model_copy(update={**plans, "preferences": prefs, "concerns": concerns})


def _kind_of(engagement: EngagementType) -> ListingKind:
    return "job" if engagement in JOB_ENGAGEMENTS else "gig"


def _serves(board: Board, strategy: SeekerStrategy) -> bool:
    if not any(strategy.plan_for(k) for k in board.kinds):
        return False
    wanted = {strategy.discipline, *strategy.secondary_disciplines}
    return not board.disciplines or bool(wanted & set(board.disciplines))


def _screen_rules(strategy: SeekerStrategy, recency_days: int) -> ScreenRules:
    prefs = strategy.preferences
    return ScreenRules(
        cv=strategy.cv,
        places=prefs,
        work_auth_blockers=strategy.work_auth_blockers,
        skills=strategy,
        blocklist={
            "industry_blocklist": prefs.excluded_industries,
            "attribute_blocklist": [*prefs.excluded_company_attributes, *prefs.excluded_companies],
        },
        recency_days=recency_days,
    )


def load_boards(path: Path) -> list[Board]:
    return TypeAdapter(list[Board]).validate_python(json.loads(path.read_text()))


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
