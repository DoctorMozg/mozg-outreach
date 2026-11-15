"""Freelance search as phases: plan, gate, sources, scout, screen, judge, score and identify."""

from collections import Counter

from pydantic import BaseModel, Field

from mozg_outreach.domain import FieldSpec
from mozg_outreach.pipelines.freelance.models import (
    FreelanceBrief,
    FreelanceStrategy,
    Gig,
    GigJudgment,
)
from mozg_outreach.pipelines.freelance.search import (
    FreelanceSearch,
    GigJudgments,
    SourcedGig,
    load_catalog,
    to_candidate,
)
from mozg_outreach.pipelines.leads.enrichment import IDENTITY, LeadEnrichment, LeadTask, RunRef
from mozg_outreach.pipelines.leads.fields import FieldTask, LeadFieldResearch, field_task
from mozg_outreach.pipelines.leads.identify import lead_input
from mozg_outreach.pipelines.leads.models import LeadIdentity
from mozg_outreach.pipelines.seeker.models import Excluded, JobSource
from mozg_outreach.pipelines.summary import (
    RunSummary,
    SeekerRunSummarizer,
    SeekerSummaryInput,
    stored_items,
)
from mozg_outreach.runs.flow import Approval, FlowContext, FlowSpec, Phase, PhaseResult, RunState
from mozg_outreach.runs.inputs import ProjectInputs
from mozg_outreach.runs.job import JOB_CANONICAL, MAX_EXCLUDED_KEPT
from mozg_outreach.storage.models import Item
from mozg_outreach.storage.repositories import ItemRepository

FREELANCE_CATALOG = JOB_CANONICAL.with_name("freelance_canonical.json")
GIG, GIG_JUDGMENT = "gig", "judgment"


class FreelanceOptions(BaseModel):
    max_boards: int = Field(default=3, ge=1, le=12, description="Boards scouted by agents.")
    research_fields_top: int = Field(
        default=10, ge=0, le=30, description="Best leads whose report fields are researched."
    )
    summary: bool = Field(default=True, description="Summarize patterns across the scored leads.")


class FreelanceSearchState(RunState):
    options: FreelanceOptions = Field(default_factory=FreelanceOptions)
    strategy: FreelanceStrategy | None = None
    boards: list[JobSource] = Field(default_factory=list)
    scouted: list[SourcedGig] = Field(default_factory=list)
    kept: list[Gig] = Field(default_factory=list)
    excluded: list[Excluded] = Field(default_factory=list)
    judgments: GigJudgments | None = None
    item_ids: list[int] = Field(default_factory=list)
    summary: RunSummary | None = None


type Ctx = FlowContext[FreelanceSearchState]


def _brief(ctx: Ctx) -> FreelanceBrief:
    return FreelanceBrief.model_validate(ProjectInputs.load(ctx.db, ctx.project_id).require_brief())


def _strategy(ctx: Ctx) -> FreelanceStrategy:
    if ctx.state.strategy is None:
        raise ValueError("no strategy yet; the plan phase must run first")
    return ctx.state.strategy


def _search(ctx: Ctx) -> FreelanceSearch:
    return FreelanceSearch(ctx.steps, load_catalog(FREELANCE_CATALOG))


async def plan(ctx: Ctx) -> PhaseResult:
    ctx.state.strategy = await _search(ctx).plan(_brief(ctx), ctx.state.feedback)
    ctx.emit("strategy", {"concerns": ctx.state.strategy.concerns})
    return None


approve_strategy = Approval[FreelanceSearchState](
    kind="strategy", back_to="plan", field="strategy", model=FreelanceStrategy
)


async def sources(ctx: Ctx) -> PhaseResult:
    search, strategy = _search(ctx), _strategy(ctx)
    boards = await search.sources(strategy)
    ranked = sorted(boards, key=lambda b: b.relevance_score, reverse=True)
    ctx.state.boards = ranked[: ctx.state.options.max_boards]
    guidance = [g.model_dump(mode="json") for g in search.guidance(strategy)]
    ctx.emit("sources", {"names": [b.name for b in ctx.state.boards], "guidance": guidance})
    return None


async def scout(ctx: Ctx) -> PhaseResult:
    outcome = await _search(ctx).scout(_strategy(ctx), ctx.state.boards, _brief(ctx))
    ctx.state.scouted = outcome.gigs
    ctx.state.concerns += outcome.concerns
    ctx.emit("collected", {"gigs": len(outcome.gigs)})
    return None


async def screen(ctx: Ctx) -> PhaseResult:
    brief = _brief(ctx)
    screening = _search(ctx).screen(ctx.state.scouted, _strategy(ctx), brief, ctx.today)
    ctx.state.kept = screening.kept[: brief.limit]
    ctx.state.excluded = screening.excluded[:MAX_EXCLUDED_KEPT]
    reasons = Counter(e.reason for e in screening.excluded)
    over = len(screening.kept) - len(ctx.state.kept)
    ctx.emit(
        "screened", {"kept": len(ctx.state.kept), "over_limit": over, "excluded": dict(reasons)}
    )
    return None


async def judge(ctx: Ctx) -> PhaseResult:
    if ctx.state.kept:
        ctx.state.judgments = await _search(ctx).judge(ctx.state.kept, _strategy(ctx))
        ctx.state.concerns += ctx.state.judgments.concerns
    return None


async def score(ctx: Ctx) -> PhaseResult:
    judgments = ctx.state.judgments or GigJudgments(by_key={}, concerns=[])
    ranked = _search(ctx).rank(ctx.state.kept, judgments, _strategy(ctx), ctx.today)
    with ctx.db.session() as session:
        items = ItemRepository(session)
        ctx.state.item_ids = []
        for entry in ranked:
            item = items.upsert(ctx.project_id, ctx.run_id, to_candidate(entry.gig))
            items.set_detail(item, GIG, entry.gig)
            items.set_detail(item, GIG_JUDGMENT, entry.judgment)
            items.set_score(item, entry.score)
            ctx.state.item_ids.append(item.id)
    ctx.emit("scored", {"items": len(ranked)})
    return None


async def identify(ctx: Ctx) -> PhaseResult:
    """Basic enrichment: the client and named people of every scored gig."""
    boards = _board_names()
    with ctx.db.session() as session:
        stored = [session.get(Item, i) for i in ctx.state.item_ids]
        tasks = [_gig_task(item, boards) for item in stored if item is not None]
    report = await LeadEnrichment(ctx.db, ctx.steps).run(
        tasks, RunRef(project_id=ctx.project_id, run_id=ctx.run_id)
    )
    ctx.state.concerns += report.concerns
    ctx.emit("identified", {"companies": report.companies, "people": report.people})
    return None


async def fields(ctx: Ctx) -> PhaseResult:
    """Research the project's report fields for the best-scored gigs."""
    spec = ProjectInputs.load(ctx.db, ctx.project_id).spec
    top = ctx.state.options.research_fields_top
    if spec is None or top == 0:
        return None
    with ctx.db.session() as session:
        stored = [session.get(Item, i) for i in ctx.state.item_ids[:top]]
        boards = _board_names()
        tasks = [_field_task(item, spec, boards) for item in stored if item is not None]
    ctx.state.concerns += await LeadFieldResearch(ctx.db, ctx.steps).run(tasks)
    ctx.emit("fields_researched", {"items": len(tasks)})
    return None


async def summarize(ctx: Ctx) -> PhaseResult:
    """Patterns and next steps across the scored leads, cited by item id."""
    if not ctx.state.options.summary:
        return None
    items = stored_items(ctx.db, ctx.state.item_ids)
    if not items:
        return None
    payload = SeekerSummaryInput.build("freelance", _brief(ctx).preferences, _strategy(ctx), items)
    outcome = await SeekerRunSummarizer(ctx.steps).summarize(payload)
    ctx.state.summary = outcome.summary
    ctx.state.concerns += outcome.concerns
    patterns = len(outcome.summary.patterns) if outcome.summary else 0
    ctx.emit("summary", {"patterns": patterns})
    return None


def _field_task(item: Item, spec: FieldSpec, boards: frozenset[str]) -> FieldTask:
    found = item.details.get(IDENTITY)
    identity = LeadIdentity.model_validate(found) if found else None
    return field_task(item.id, _gig_task(item, boards).lead, identity, spec)


def _board_names() -> frozenset[str]:
    catalog = load_catalog(FREELANCE_CATALOG)
    return frozenset(b.name.casefold() for b in [*catalog.scrape, *catalog.guidance])


def _gig_task(item: Item, boards: frozenset[str]) -> LeadTask:
    gig, _ = gig_of(item)
    hidden = boards | {s.casefold() for s in gig.listing.sources}
    lead = lead_input(gig.listing, "freelance", gig.terms.scope, hidden)
    return LeadTask(item_id=item.id, listing=gig.listing, lead=lead)


def gig_of(item: Item) -> tuple[Gig, GigJudgment | None]:
    judged = item.details.get(GIG_JUDGMENT)
    return (
        Gig.model_validate(item.details[GIG]),
        GigJudgment.model_validate(judged) if judged else None,
    )


FREELANCE_SEARCH = FlowSpec[FreelanceSearchState](
    pipeline="freelance.search",
    state_type=FreelanceSearchState,
    phases=(
        Phase("plan", plan),
        Phase("approve_strategy", approve_strategy),
        Phase("sources", sources),
        Phase("scout", scout),
        Phase("screen", screen),
        Phase("judge", judge),
        Phase("score", score),
        Phase("identify", identify),
        Phase("fields", fields),
        Phase("summarize", summarize),
    ),
)
