"""Biz search as phases: plan, strategy gate, sources, scout, assess and score, then summarize."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from pydantic import BaseModel, Field

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.domain import ItemCandidate
from mozg_outreach.pipelines.biz.assessment import BizAssessment
from mozg_outreach.pipelines.biz.assessment_models import AssessmentContext
from mozg_outreach.pipelines.biz.directories import Directories, default_catalog
from mozg_outreach.pipelines.biz.discovery import BizDiscovery, DiscoveryError
from mozg_outreach.pipelines.biz.duplicates import CompanyDuplicates
from mozg_outreach.pipelines.biz.models import (
    BizBrief,
    ClimateReport,
    DirectorySource,
    Strategy,
    StrategyInput,
)
from mozg_outreach.pipelines.biz.outreach import ASSESSMENT, stored_candidate
from mozg_outreach.pipelines.biz.registry_ingest import BizRegistryIngest
from mozg_outreach.pipelines.biz.summary import BizRunSummarizer, SummaryRequest, summary_item
from mozg_outreach.pipelines.summary import RunSummary, best_first
from mozg_outreach.registry.ingest import IngestContext
from mozg_outreach.runs.auto_enrich import start_enrichments
from mozg_outreach.runs.flow import Approval, FlowContext, FlowSpec, Phase, PhaseResult, RunState
from mozg_outreach.runs.inputs import ProjectInputs
from mozg_outreach.scoring.biz import BizScoreInput, BizScorer
from mozg_outreach.sources.base import USER_AGENT
from mozg_outreach.storage.db import Database
from mozg_outreach.storage.models import Item
from mozg_outreach.storage.repositories import ItemRepository

logger = logging.getLogger(__name__)

DIRECTORY_TIMEOUT_S = 30.0


class BizOptions(BaseModel):
    limit: int = Field(default=50, ge=1, le=100)
    region: str | None = Field(default=None, description="Hard filter over the brief's regions.")
    climate: bool = Field(default=True, description="Research the market climate first.")
    sources_per_sector: int = Field(
        default=3, ge=1, le=3, description="Directories found and scouted for each target sector."
    )
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


@asynccontextmanager
async def _discovery(ctx: Ctx) -> AsyncIterator[BizDiscovery]:
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, timeout=DIRECTORY_TIMEOUT_S, follow_redirects=True
    ) as client:
        yield BizDiscovery(ctx.steps, Directories(client, default_catalog()))


async def sources(ctx: Ctx) -> PhaseResult:
    async with _discovery(ctx) as discovery:
        found = await discovery.find_sources(
            _strategy(ctx), _region(ctx, _brief(ctx)), ctx.state.options.sources_per_sector
        )
    ctx.state.sources = found.sources
    ctx.state.concerns += found.concerns
    ctx.emit("sources", {"names": [s.name for s in found.sources]})
    return None


async def scout(ctx: Ctx) -> PhaseResult:
    async with _discovery(ctx) as discovery:
        outcome = await discovery.scout(
            _strategy(ctx),
            ctx.state.sources,
            region=_region(ctx, _brief(ctx)),
            limit=ctx.state.options.limit,
        )
    ctx.state.concerns += outcome.concerns
    with ctx.db.session() as session:
        # Earlier runs may have saved a company under its domain or an older form of its name.
        known = CompanyDuplicates(session, ctx.project_id).known(outcome.candidates)
        items = ItemRepository(session)
        # Assessment is paid per company, and an archived lead stays as the user left it.
        archived = items.archived_keys(ctx.project_id)
        ctx.state.candidates = [c for c in known if c.dedup_key not in archived]
        if len(ctx.state.candidates) < len(known):
            logger.info("skipping %d archived companies", len(known) - len(ctx.state.candidates))
        ctx.state.item_ids = [
            items.upsert(ctx.project_id, ctx.run_id, c).id for c in ctx.state.candidates
        ]
    ctx.emit("items", {"found": len(ctx.state.item_ids), "by_sector": outcome.mix})
    return None


def _blocker(db: Database, project_id: int) -> str | None:
    inputs = ProjectInputs.load(db, project_id)
    if not inputs.brief:
        return "write the brief first"
    if inputs.spec is None:
        return "accept the report fields on the Fields tab before running the search"
    return None


async def assess(ctx: Ctx) -> PhaseResult:
    spec = ProjectInputs.load(ctx.db, ctx.project_id).spec
    if spec is None:
        raise ValueError("accept the report fields on the Fields tab before running the search")
    strategy = _strategy(ctx)
    results = await BizAssessment(ctx.steps).assess_many(
        ctx.state.candidates, AssessmentContext(strategy=strategy, spec=spec)
    )
    with ctx.db.session() as session:
        items = ItemRepository(session)
        ingest = BizRegistryIngest(session)
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
            # Every scored company joins the registry now; enrichment later adds its people.
            context = IngestContext(project_id=ctx.project_id, run_id=ctx.run_id, item_id=item.id)
            ingest.company(stored_candidate(item), context)
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
    items = best_first(items)
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


async def auto_enrich(ctx: Ctx) -> PhaseResult:
    start_enrichments(ctx, ctx.state.item_ids)
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
        Phase("auto_enrich", auto_enrich),
    ),
    blocker=_blocker,
)
