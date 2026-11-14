"""Freelance search as phases: plan, strategy gate, sources, scout, screen, judge and score."""

from collections import Counter

from pydantic import BaseModel, Field

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
from mozg_outreach.pipelines.job.models import Excluded, JobSource
from mozg_outreach.runs.flow import Approval, FlowContext, FlowSpec, Phase, PhaseResult, RunState
from mozg_outreach.runs.inputs import ProjectInputs
from mozg_outreach.runs.job import JOB_CANONICAL, MAX_EXCLUDED_KEPT
from mozg_outreach.storage.models import Item
from mozg_outreach.storage.repositories import ItemRepository

FREELANCE_CATALOG = JOB_CANONICAL.with_name("freelance_canonical.json")
GIG, GIG_JUDGMENT = "gig", "judgment"


class FreelanceOptions(BaseModel):
    max_boards: int = Field(default=3, ge=1, le=12, description="Boards scouted by agents.")


class FreelanceSearchState(RunState):
    options: FreelanceOptions = Field(default_factory=FreelanceOptions)
    strategy: FreelanceStrategy | None = None
    boards: list[JobSource] = Field(default_factory=list)
    scouted: list[SourcedGig] = Field(default_factory=list)
    kept: list[Gig] = Field(default_factory=list)
    excluded: list[Excluded] = Field(default_factory=list)
    judgments: GigJudgments | None = None


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


def _revise(state: FreelanceSearchState, revised: dict[str, object]) -> None:
    state.strategy = FreelanceStrategy.model_validate(revised)


approve_strategy = Approval[FreelanceSearchState](
    kind="strategy",
    back_to="plan",
    show=lambda s: {"strategy": s.strategy.model_dump(mode="json") if s.strategy else None},
    revise=_revise,
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
    ctx.emit("screened", {"kept": len(ctx.state.kept), "excluded": dict(reasons)})
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
        for entry in ranked:
            item = items.upsert(ctx.project_id, ctx.run_id, to_candidate(entry.gig))
            items.set_detail(item, GIG, entry.gig)
            items.set_detail(item, GIG_JUDGMENT, entry.judgment)
            items.set_score(item, entry.score)
    ctx.emit("scored", {"items": len(ranked)})
    return None


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
    ),
)
