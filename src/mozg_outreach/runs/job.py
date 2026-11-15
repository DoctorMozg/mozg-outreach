"""Job search as phases: plan, strategy gate, sources, Telegram channels, collect, screen, judge,
score, identify and report fields."""

import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy import select

from mozg_outreach.domain import FieldSpec
from mozg_outreach.pipelines.leads.enrichment import IDENTITY, LeadEnrichment, LeadTask, RunRef
from mozg_outreach.pipelines.leads.fields import FieldTask, LeadFieldResearch, field_task
from mozg_outreach.pipelines.leads.identify import lead_input
from mozg_outreach.pipelines.leads.models import LeadIdentity
from mozg_outreach.pipelines.seeker.feeds import FeedRequest, JobFeeds
from mozg_outreach.pipelines.seeker.judge import Judgments
from mozg_outreach.pipelines.seeker.listings import SourcedListing
from mozg_outreach.pipelines.seeker.models import (
    Excluded,
    JobBrief,
    JobSource,
    JobStrategy,
    Listing,
    ListingJudgment,
)
from mozg_outreach.pipelines.seeker.search import JobSearch, load_canonical, to_candidate
from mozg_outreach.pipelines.seeker.telegram_posts import TelegramPosts, TelegramRequest
from mozg_outreach.pipelines.summary import (
    RunSummary,
    SeekerRunSummarizer,
    SeekerSummaryInput,
    stored_items,
)
from mozg_outreach.runs.flow import Approval, FlowContext, FlowSpec, Phase, PhaseResult, RunState
from mozg_outreach.runs.inputs import ProjectInputs
from mozg_outreach.sources.telegram import TelegramAdapter, TelegramConfig, channel_username
from mozg_outreach.sources.telegram_discovery import (
    MAX_QUERIES,
    ChannelDiscovery,
    DiscoveryRequest,
    FoundChannel,
)
from mozg_outreach.storage.models import Item, SourceConfig
from mozg_outreach.storage.repositories import ItemRepository

logger = logging.getLogger(__name__)

JOB_CANONICAL = (
    Path(__file__).resolve().parents[3] / "prompts" / "seeker" / "sources" / "job_canonical.json"
)
WEB_SEARCH = "web_search"
LISTING, JUDGMENT, CONTACTS = "listing", "judgment", "contacts"
MAX_EXCLUDED_KEPT = 100


class JobOptions(BaseModel):
    max_boards: int = Field(default=4, ge=0, le=15, description="Web boards scouted by agents.")
    research_fields_top: int = Field(
        default=10, ge=0, le=30, description="Best leads whose report fields are researched."
    )
    summary: bool = Field(default=True, description="Summarize patterns across the scored leads.")


class ChannelChoice(BaseModel):
    channels: list[FoundChannel]


class JobSearchState(RunState):
    options: JobOptions = Field(default_factory=JobOptions)
    strategy: JobStrategy | None = None
    boards: list[JobSource] = Field(default_factory=list)
    found: ChannelChoice | None = Field(default=None, description="Telegram channels to approve.")
    offered: list[str] = Field(default_factory=list, description="Usernames shown at the gate.")
    scouted: list[SourcedListing] = Field(default_factory=list)
    kept: list[Listing] = Field(default_factory=list)
    excluded: list[Excluded] = Field(default_factory=list)
    judgments: Judgments | None = None
    item_ids: list[int] = Field(default_factory=list)
    summary: RunSummary | None = None


type Ctx = FlowContext[JobSearchState]


def _brief(ctx: Ctx) -> JobBrief:
    return JobBrief.model_validate(ProjectInputs.load(ctx.db, ctx.project_id).require_brief())


def _strategy(ctx: Ctx) -> JobStrategy:
    if ctx.state.strategy is None:
        raise ValueError("no strategy yet; the plan phase must run first")
    return ctx.state.strategy


def _search(ctx: Ctx) -> JobSearch:
    return JobSearch(ctx.steps, load_canonical(JOB_CANONICAL))


def _sources(ctx: Ctx) -> list[SourceConfig]:
    with ctx.db.session() as session:
        return list(
            session.scalars(
                select(SourceConfig).where(
                    SourceConfig.project_id == ctx.project_id, SourceConfig.enabled.is_(True)
                )
            )
        )


async def plan(ctx: Ctx) -> PhaseResult:
    ctx.state.strategy = await _search(ctx).plan(_brief(ctx), ctx.state.feedback)
    ctx.emit("strategy", {"concerns": ctx.state.strategy.concerns})
    return None


approve_strategy = Approval[JobSearchState](
    kind="strategy", back_to="plan", field="strategy", model=JobStrategy
)


async def sources(ctx: Ctx) -> PhaseResult:
    web = any(s.kind == WEB_SEARCH for s in _sources(ctx))
    if not web or ctx.state.options.max_boards == 0:
        ctx.state.boards = []
        return None
    boards = await _search(ctx).sources(_strategy(ctx))
    ranked = sorted(boards, key=lambda b: b.relevance_score, reverse=True)
    ctx.state.boards = ranked[: ctx.state.options.max_boards]
    ctx.emit("sources", {"names": [b.name for b in ctx.state.boards]})
    return None


async def discover_channels(ctx: Ctx) -> PhaseResult:
    """Search Telegram for job channels matching the strategy's skills, for the user to approve."""
    ctx.state.found, ctx.state.offered = None, []
    rows = _discovering(ctx)
    if not rows:
        return None
    configs = [TelegramConfig.model_validate(r.config) for r in rows]
    room = configs[0].max_channels - len(configs[0].channels)
    if room <= 0:
        return None
    adapter = ctx.adapters(TelegramAdapter.name, rows[0].config)
    if not isinstance(adapter, TelegramAdapter):
        raise TypeError(f"telegram source built {type(adapter).__name__}, not TelegramAdapter")
    known = {
        channel_username(u).casefold() for cfg in configs for u in [*cfg.channels, *cfg.excluded]
    }
    request = DiscoveryRequest(
        terms=_discovery_terms(_strategy(ctx)), known=known, limit=room, now=datetime.now(UTC)
    )
    async with adapter.open_session() as session:
        result = await ChannelDiscovery(session).find(request)
    ctx.state.concerns += result.concerns
    if result.channels:
        ctx.state.found = ChannelChoice(channels=result.channels)
        ctx.state.offered = [c.username for c in result.channels]
    ctx.emit("channels_found", {"usernames": ctx.state.offered})
    return None


_channel_gate = Approval[JobSearchState](
    kind="channels", back_to="discover_channels", field="found", model=ChannelChoice
)


async def approve_channels(ctx: Ctx) -> PhaseResult:
    if ctx.state.found is None:
        return None
    outcome = await _channel_gate(ctx)
    if outcome is None:
        _save_channels(ctx, ctx.state.found)
    return outcome


def _discovery_terms(strategy: JobStrategy) -> list[str]:
    return list(dict.fromkeys([*strategy.skills(), *strategy.cv.top_titles]))[:MAX_QUERIES]


def _discovering(ctx: Ctx) -> list[SourceConfig]:
    return [
        r
        for r in _sources(ctx)
        if r.kind == TelegramAdapter.name and TelegramConfig.model_validate(r.config).discover
    ]


def _save_channels(ctx: Ctx, choice: ChannelChoice) -> None:
    """Approved channels join the source; the rest are excluded so they aren't offered again."""
    picked = [c.username for c in choice.channels]
    taken = {u.casefold() for u in picked}
    dropped = [u for u in ctx.state.offered if u.casefold() not in taken]
    with ctx.db.session() as session:
        for row in session.scalars(
            select(SourceConfig).where(
                SourceConfig.project_id == ctx.project_id,
                SourceConfig.kind == TelegramAdapter.name,
            )
        ):
            config = TelegramConfig.model_validate(row.config)
            if config.discover:
                channels, excluded = [*config.channels, *picked], [*config.excluded, *dropped]
                merged = config.model_copy(update={"channels": channels, "excluded": excluded})
                row.config = merged.model_dump(mode="json")
    logger.info("telegram channels: %d added, %d excluded", len(picked), len(dropped))
    ctx.emit("channels_saved", {"added": picked, "excluded": dropped})


async def collect(ctx: Ctx) -> PhaseResult:
    strategy, brief = _strategy(ctx), _brief(ctx)
    rows = [s for s in _sources(ctx) if s.kind not in {WEB_SEARCH, TelegramAdapter.name}]
    scouted: list[SourcedListing] = []
    for row in (s for s in _sources(ctx) if s.kind == TelegramAdapter.name):
        posts = await TelegramPosts(ctx.steps).collect(
            ctx.adapters(row.kind, row.config),
            TelegramRequest(
                skills=strategy.skills(),
                kinds=["job"],
                oldest=ctx.today - timedelta(days=brief.recency_days),
                cursor=row.cursor,
            ),
        )
        scouted += posts.listings
        ctx.state.concerns += posts.concerns
        _save_cursors(ctx, posts.cursors)
    if rows:
        titles = strategy.cv.top_titles
        keywords = [*titles, *(a for t in titles for a in strategy.title_aliases.get(t, []))]
        feeds = await JobFeeds([ctx.adapters(r.kind, r.config) for r in rows]).collect(
            FeedRequest(
                keywords=keywords,
                recency_days=brief.recency_days,
                today=ctx.today,
                cursors={r.kind: r.cursor for r in rows},
            )
        )
        scouted += feeds.listings
        ctx.state.concerns += feeds.concerns
        _save_cursors(ctx, feeds.cursors)
    if ctx.state.boards:
        outcome = await _search(ctx).scout(strategy, ctx.state.boards, brief)
        scouted += outcome.listings
        ctx.state.concerns += outcome.concerns
    ctx.state.scouted = scouted
    ctx.emit("collected", {"listings": len(scouted)})
    return None


def _save_cursors(ctx: Ctx, cursors: dict[str, str | None]) -> None:
    with ctx.db.session() as session:
        rows = session.scalars(
            select(SourceConfig).where(SourceConfig.project_id == ctx.project_id)
        )
        for row in rows:
            if row.kind in cursors and cursors[row.kind] is not None:
                row.cursor = cursors[row.kind]


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
    judgments = ctx.state.judgments or Judgments(by_key={}, concerns=[])
    ranked = _search(ctx).rank(ctx.state.kept, judgments, _strategy(ctx), ctx.today)
    with ctx.db.session() as session:
        items = ItemRepository(session)
        ctx.state.item_ids = []
        for entry in ranked:
            item = items.upsert(ctx.project_id, ctx.run_id, to_candidate(entry.listing))
            items.set_detail(item, LISTING, entry.listing)
            items.set_detail(item, JUDGMENT, entry.judgment)
            items.set_score(item, entry.score)
            ctx.state.item_ids.append(item.id)
    ctx.emit("scored", {"items": len(ranked)})
    return None


async def identify(ctx: Ctx) -> PhaseResult:
    """Basic enrichment: the employer and named people of every scored listing."""
    with ctx.db.session() as session:
        stored = [session.get(Item, i) for i in ctx.state.item_ids]
        tasks = [_listing_task(item) for item in stored if item is not None]
    report = await LeadEnrichment(ctx.db, ctx.steps).run(
        tasks, RunRef(project_id=ctx.project_id, run_id=ctx.run_id)
    )
    ctx.state.concerns += report.concerns
    ctx.emit("identified", {"companies": report.companies, "people": report.people})
    return None


async def fields(ctx: Ctx) -> PhaseResult:
    """Research the project's report fields for the best-scored listings."""
    spec = ProjectInputs.load(ctx.db, ctx.project_id).spec
    top = ctx.state.options.research_fields_top
    if spec is None or top == 0:
        return None
    with ctx.db.session() as session:
        stored = [session.get(Item, i) for i in ctx.state.item_ids[:top]]
        tasks = [_field_task(item, spec) for item in stored if item is not None]
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
    payload = SeekerSummaryInput.build("job", _brief(ctx).preferences, _strategy(ctx), items)
    outcome = await SeekerRunSummarizer(ctx.steps).summarize(payload)
    ctx.state.summary = outcome.summary
    ctx.state.concerns += outcome.concerns
    patterns = len(outcome.summary.patterns) if outcome.summary else 0
    ctx.emit("summary", {"patterns": patterns})
    return None


def _field_task(item: Item, spec: FieldSpec) -> FieldTask:
    found = item.details.get(IDENTITY)
    identity = LeadIdentity.model_validate(found) if found else None
    return field_task(item.id, _listing_task(item).lead, identity, spec)


def _listing_task(item: Item) -> LeadTask:
    listing, _ = listing_of(item)
    return LeadTask(
        item_id=item.id, listing=listing, lead=lead_input(listing, "job", listing.summary)
    )


def listing_of(item: Item) -> tuple[Listing, ListingJudgment | None]:
    listing = Listing.model_validate(item.details[LISTING])
    judged = item.details.get(JUDGMENT)
    return listing, ListingJudgment.model_validate(judged) if judged else None


JOB_SEARCH = FlowSpec[JobSearchState](
    pipeline="job.search",
    state_type=JobSearchState,
    phases=(
        Phase("plan", plan),
        Phase("approve_strategy", approve_strategy),
        Phase("sources", sources),
        Phase("discover_channels", discover_channels),
        Phase("approve_channels", approve_channels),
        Phase("collect", collect),
        Phase("screen", screen),
        Phase("judge", judge),
        Phase("score", score),
        Phase("identify", identify),
        Phase("fields", fields),
        Phase("summarize", summarize),
    ),
)
