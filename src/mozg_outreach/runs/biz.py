"""Biz search as phases: plan, strategy gate, sources, scout, assess and score, then summarize."""

import logging

from pydantic import BaseModel, Field

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.domain import ItemCandidate
from mozg_outreach.pipelines.biz.assessment import BizAssessment
from mozg_outreach.pipelines.biz.assessment_models import AssessmentContext
from mozg_outreach.pipelines.biz.discovery import BizDiscovery, DiscoveryError
from mozg_outreach.pipelines.biz.models import (
    BizBrief,
    ClimateReport,
    DirectorySource,
    Strategy,
    StrategyInput,
)
from mozg_outreach.pipelines.biz.outreach import ASSESSMENT
from mozg_outreach.pipelines.biz.summary import (
    BizRunSummarizer,
    RunSummary,
    SummaryRequest,
    summary_item,
)
from mozg_outreach.runs.flow import Approval, FlowContext, FlowSpec, Phase, PhaseResult, RunState
from mozg_outreach.runs.inputs import ProjectInputs
from mozg_outreach.scoring.biz import BizScoreInput, BizScorer
from mozg_outreach.storage.models import Item
from mozg_outreach.storage.repositories import ItemRepository

logger = logging.getLogger(__name__)


class BizOptions(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)
    region: str | None = Field(default=None, description="Hard filter over the brief's regions.")
    climate: bool = Field(default=False, description="Research the market climate first.")
    max_sources: int = Field(default=4, ge=1, le=10, description="Directories scouted by agents.")
    summary: bool = Field(default=True, description="Summarize patterns across the scored leads.")


class BizSearchState(RunState):
    options: BizOptions = Field(default_factory=BizOptions)
    climate: ClimateReport | None = None
    strategy: Strategy | None = None
    sources: list[DirectorySource] = Field(default_factory=list)
    candidates: list[ItemCandidate] = Field(default_factory=list)
    item_ids: list[int] = Field(default_factory=list)
    summary: RunSummary | None = None


type Ctx = FlowContext[BizSearchState]


def _brief(ctx: Ctx) -> BizBrief:
    return BizBrief.model_validate(ProjectInputs.load(ctx.db, ctx.project_id).require_brief())


def _strategy(ctx: Ctx) -> Strategy:
    if ctx.state.strategy is None:
        raise ValueError("no strategy yet; the plan phase must run first")
    return ctx.state.strategy


def _region(ctx: Ctx, brief: BizBrief) -> str | None:
    return ctx.state.options.region or ", ".join(brief.geography.target_regions) or None


async def plan(ctx: Ctx) -> PhaseResult:
    brief = _brief(ctx)
    discovery = BizDiscovery(ctx.steps)
    region = _region(ctx, brief)
    if ctx.state.options.climate and ctx.state.climate is None and region:
        try:
            ctx.state.climate = await discovery.research_climate(brief, region)
        except DiscoveryError as exc:
            ctx.state.concerns.append(f"Climate research skipped: {exc}")
    ctx.state.strategy = await discovery.plan_strategy(
        StrategyInput(
            goal=brief.goal.goal,
            region=ctx.state.options.region,
            brief=brief,
            climate=ctx.state.climate,
            feedback=ctx.state.feedback,
        )
    )
    ctx.emit("strategy", {"concerns": ctx.state.strategy.concerns})
    return None


approve_strategy = Approval[BizSearchState](
    kind="strategy", back_to="plan", field="strategy", model=Strategy
)


async def sources(ctx: Ctx) -> PhaseResult:
    found = await BizDiscovery(ctx.steps).find_sources(_strategy(ctx), _region(ctx, _brief(ctx)))
    ctx.state.sources = found.sources[: ctx.state.options.max_sources]
    ctx.state.concerns += found.concerns
    ctx.emit("sources", {"names": [s.name for s in found.sources]})
    return None


async def scout(ctx: Ctx) -> PhaseResult:
    outcome = await BizDiscovery(ctx.steps).scout(
        _strategy(ctx),
        ctx.state.sources,
        region=_region(ctx, _brief(ctx)),
        limit=ctx.state.options.limit,
    )
    ctx.state.candidates = outcome.candidates[: ctx.state.options.limit]
    ctx.state.concerns += outcome.concerns
    with ctx.db.session() as session:
        items = ItemRepository(session)
        ctx.state.item_ids = [
            items.upsert(ctx.project_id, ctx.run_id, c).id for c in ctx.state.candidates
        ]
    ctx.emit("items", {"found": len(ctx.state.item_ids)})
    return None


async def assess(ctx: Ctx) -> PhaseResult:
    spec = ProjectInputs.load(ctx.db, ctx.project_id).spec
    if spec is None:
        raise ValueError("accept a field spec before running the search")
    strategy = _strategy(ctx)
    results = await BizAssessment(ctx.steps).assess_many(
        ctx.state.candidates, AssessmentContext(strategy=strategy, spec=spec)
    )
    with ctx.db.session() as session:
        items = ItemRepository(session)
        pairs = zip(ctx.state.item_ids, ctx.state.candidates, results, strict=True)
        for item_id, candidate, result in pairs:
            item = session.get(Item, item_id)
            if item is None:
                continue  # deleted by the user mid-run
            items.merge_fields(item, result.fields)
            items.set_detail(item, ASSESSMENT, result)
            score = BizScorer().score(
                BizScoreInput(
                    dossier=None,
                    verdict=result.verdict,
                    signals=strategy.search_signals,
                    fields={**candidate.fields, **result.fields},
                    spec=spec,
                    weights=strategy.scoring_weights,
                )
            )
            items.set_score(item, score)
    ctx.emit("scored", {"items": len(results)})
    return None


async def summarize(ctx: Ctx) -> PhaseResult:
    if not ctx.state.options.summary:
        return None
    with ctx.db.session() as session:
        found = [session.get(Item, i) for i in ctx.state.item_ids]
        items = [summary_item(item) for item in found if item is not None]
    if not items:
        return None
    items.sort(key=lambda i: -1 if i.score is None else i.score, reverse=True)
    try:
        summary = await BizRunSummarizer(ctx.steps).summarize(
            SummaryRequest(strategy=_strategy(ctx), items=items)
        )
    except (AgentRunError, AgentOutputError) as exc:
        # The report is complete without a summary, so a failed one doesn't fail the run.
        logger.warning("run summary failed: %s", exc)
        ctx.state.concerns.append(f"No run summary: {exc}")
        return None
    ctx.state.summary = summary
    ctx.state.concerns += summary.concerns
    ctx.emit("summary", {"patterns": len(summary.patterns)})
    return None


BIZ_SEARCH = FlowSpec[BizSearchState](
    pipeline="biz.search",
    state_type=BizSearchState,
    phases=(
        Phase("plan", plan),
        Phase("approve_strategy", approve_strategy),
        Phase("sources", sources),
        Phase("scout", scout),
        Phase("assess", assess),
        Phase("summarize", summarize),
    ),
)
