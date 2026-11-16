"""One person's search for jobs and gigs as phases: plan, strategy gate, boards, Telegram
channels, collect, screen, judge, score, identify, report fields and summary."""

import hashlib
import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from mozg_outreach.domain import FieldSpec, ItemScore, RunStatus
from mozg_outreach.pipelines.leads.enrichment import IDENTITY, LeadEnrichment, LeadTask, RunRef
from mozg_outreach.pipelines.leads.fields import FieldTask, LeadFieldResearch, field_task
from mozg_outreach.pipelines.leads.identify import lead_input
from mozg_outreach.pipelines.leads.models import LeadIdentity
from mozg_outreach.pipelines.seeker.feeds import FeedRequest, JobFeeds
from mozg_outreach.pipelines.seeker.judge import Judgments
from mozg_outreach.pipelines.seeker.listings import SourcedListing
from mozg_outreach.pipelines.seeker.models import (
    KINDS,
    Board,
    Excluded,
    Listing,
    ListingJudgment,
    ListingKind,
    SeekerBrief,
    SeekerStrategy,
)
from mozg_outreach.pipelines.seeker.search import SeekerSearch, load_boards, to_candidate
from mozg_outreach.pipelines.seeker.telegram_posts import TelegramPosts, TelegramRequest
from mozg_outreach.pipelines.summary import (
    RunSummary,
    SeekerRunSummarizer,
    SeekerSummaryInput,
    stored_items,
)
from mozg_outreach.runs.flow import (
    Approval,
    FlowContext,
    FlowSpec,
    GateAnswer,
    Phase,
    PhaseResult,
    RunState,
)
from mozg_outreach.runs.inputs import ProjectInputs
from mozg_outreach.sources.telegram import (
    TelegramAdapter,
    TelegramConfig,
    TelegramConfigError,
    TelegramCredentials,
    channel_username,
)
from mozg_outreach.sources.telegram_discovery import (
    MAX_QUERIES,
    ChannelDiscovery,
    DiscoveryRequest,
    FoundChannel,
)
from mozg_outreach.storage.db import Database
from mozg_outreach.storage.models import Item, JsonObject, Run, SourceConfig
from mozg_outreach.storage.repositories import ItemRepository

logger = logging.getLogger(__name__)

BOARDS = Path(__file__).resolve().parents[3] / "prompts" / "seeker" / "sources" / "boards.json"
WEB_SEARCH = "web_search"
LISTING, JUDGMENT, CONTACTS = "listing", "judgment", "contacts"
MAX_EXCLUDED_KEPT = 100
# Domains a feed source already serves; a web scout of the same board only repeats its listings.
FEED_DOMAINS: dict[str, tuple[str, ...]] = {
    "remoteok": ("remoteok.com",),
    "remotive": ("remotive.com",),
    "arbeitnow": ("arbeitnow.com",),
    "hn_whoishiring": ("news.ycombinator.com", "hn.algolia.com"),
    "greenhouse": ("boards.greenhouse.io",),
    "lever": ("jobs.lever.co",),
    "ashby": ("jobs.ashbyhq.com",),
}
# Identifying an employer costs about $0.35 a lead, so only promising leads get it.
IDENTIFY_MIN_SCORE = 60


class SeekerOptions(BaseModel):
    reuse_last_strategy: bool = Field(
        default=True,
        description="Reuse the last search's strategy and boards while the brief is unchanged.",
    )
    max_boards: int = Field(default=4, ge=0, le=15, description="Web boards scouted by agents.")
    research_fields_top: int = Field(
        default=10, ge=0, le=30, description="Best leads whose report fields are researched."
    )
    summary: bool = Field(default=True, description="Summarize patterns across the scored leads.")


class ChannelChoice(BaseModel):
    channels: list[FoundChannel]


class SeekerSearchState(RunState):
    options: SeekerOptions = Field(default_factory=SeekerOptions)
    strategy: SeekerStrategy | None = None
    brief_hash: str | None = Field(
        default=None, description="The brief the strategy was planned from."
    )
    reused_from: int | None = Field(default=None, description="The run the strategy came from.")
    boards: list[Board] = Field(default_factory=list)
    guidance: list[Board] = Field(default_factory=list, description="Gated networks to apply to.")
    found: ChannelChoice | None = Field(default=None, description="Telegram channels to approve.")
    offered: list[str] = Field(default_factory=list, description="Usernames shown at the gate.")
    scouted: list[SourcedListing] = Field(default_factory=list)
    kept: list[Listing] = Field(default_factory=list)
    excluded: list[Excluded] = Field(default_factory=list)
    judgments: Judgments | None = None
    item_ids: list[int] = Field(default_factory=list)
    summary: RunSummary | None = None


type Ctx = FlowContext[SeekerSearchState]


def _brief(ctx: Ctx) -> SeekerBrief:
    return SeekerBrief.model_validate(ProjectInputs.load(ctx.db, ctx.project_id).require_brief())


def _strategy(ctx: Ctx) -> SeekerStrategy:
    if ctx.state.strategy is None:
        raise ValueError("no strategy yet; the plan phase must run first")
    return ctx.state.strategy


def _search(ctx: Ctx) -> SeekerSearch:
    return SeekerSearch(ctx.steps, load_boards(BOARDS))


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
    brief = _brief(ctx)
    # Applicant details only fill forms, so editing them keeps the planned strategy.
    planned = brief.model_dump_json(exclude={"applicant"})
    ctx.state.brief_hash = hashlib.sha256(planned.encode()).hexdigest()
    last = _last_search(ctx) if ctx.state.options.reuse_last_strategy else None
    # Gate feedback asks for a new plan, so it never reuses.
    if last and last.state.brief_hash == ctx.state.brief_hash and not ctx.state.feedback:
        ctx.state.strategy = last.state.strategy
        ctx.state.reused_from = last.state.reused_from or last.run_id
        ctx.state.boards, ctx.state.guidance = last.state.boards, last.state.guidance
        # The user approved this strategy in its own run, so its gate passes without asking again.
        ctx.state.answers[approve_strategy.kind] = GateAnswer(approve=True)
        logger.info("run %d reuses the strategy of run %d", ctx.run_id, ctx.state.reused_from)
    else:
        ctx.state.strategy = await _search(ctx).plan(brief, ctx.state.feedback)
        ctx.state.reused_from = None
    payload: JsonObject = {"concerns": _strategy(ctx).concerns}
    if ctx.state.reused_from is not None:
        payload["reused_from"] = ctx.state.reused_from
    ctx.emit("strategy", payload)
    return None


class _LastSearch(BaseModel):
    run_id: int
    state: SeekerSearchState


def _last_search(ctx: Ctx) -> _LastSearch | None:
    """The newest finished search of this project, whose strategy the user approved."""
    with ctx.db.session() as session:
        row = session.scalars(
            select(Run)
            .where(
                Run.project_id == ctx.project_id,
                Run.pipeline == SEEKER_SEARCH.pipeline,
                Run.status == RunStatus.COMPLETE,
            )
            .order_by(Run.id.desc())
            .limit(1)
        ).first()
        if row is None or not row.state:
            return None
        last = _LastSearch(run_id=row.id, state=SeekerSearchState.model_validate(row.state))
    return last if last.state.strategy else None


approve_strategy = Approval[SeekerSearchState](
    kind="strategy", back_to="plan", field="strategy", model=SeekerStrategy
)


async def boards(ctx: Ctx) -> PhaseResult:
    """The most relevant boards per kind, so one kind's boards never crowd out the other's."""
    web = any(s.kind == WEB_SEARCH for s in _sources(ctx))
    if not web or ctx.state.options.max_boards == 0:
        ctx.state.boards, ctx.state.guidance = [], []
        return None
    if ctx.state.reused_from is not None:
        return None
    choice = await _search(ctx).boards(_strategy(ctx))
    covered = _feed_domains(_sources(ctx))
    uncovered = [b for b in choice.scouted if not _served_by_feed(b, covered)]
    logger.info("skipping %d boards a feed covers", len(choice.scouted) - len(uncovered))
    ranked = sorted(uncovered, key=lambda b: b.relevance_score, reverse=True)
    picked: dict[str, Board] = {}
    for kind in KINDS:
        of_kind = [b for b in ranked if kind in b.kinds]
        picked |= {b.url: b for b in of_kind[: ctx.state.options.max_boards]}
    ctx.state.boards = list(picked.values())
    ctx.state.guidance = choice.guidance
    guidance = [g.model_dump(mode="json") for g in choice.guidance]
    ctx.emit("sources", {"names": [b.name for b in ctx.state.boards], "guidance": guidance})
    return None


def _feed_domains(sources: list[SourceConfig]) -> set[str]:
    return {d for s in sources for d in FEED_DOMAINS.get(s.kind, ())}


def _served_by_feed(board: Board, domains: set[str]) -> bool:
    url = unquote(board.url).casefold()
    return any(d in url for d in domains)


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


_channel_gate = Approval[SeekerSearchState](
    kind="channels", back_to="discover_channels", field="found", model=ChannelChoice
)


async def approve_channels(ctx: Ctx) -> PhaseResult:
    if ctx.state.found is None:
        return None
    outcome = await _channel_gate(ctx)
    if outcome is None:
        _save_channels(ctx, ctx.state.found)
    return outcome


def _discovery_terms(strategy: SeekerStrategy) -> list[str]:
    """What the user needs, not the CV's whole skill list, which pulls in off-target channels."""
    required = [*strategy.must_have_skills, *(s for g in strategy.must_have_or_groups for s in g)]
    terms = [*required, *strategy.cv.top_titles, *strategy.nice_to_have_skills]
    return list(dict.fromkeys(terms))[:MAX_QUERIES]


def _discovering(ctx: Ctx) -> list[SourceConfig]:
    return [r for r in _telegram(ctx) if TelegramConfig.model_validate(r.config).discover]


def _telegram(ctx: Ctx) -> list[SourceConfig]:
    """Enabled Telegram sources; without API keys they are skipped like a failing feed."""
    rows = [r for r in _sources(ctx) if r.kind == TelegramAdapter.name]
    if not rows:
        return []
    try:
        TelegramCredentials.from_env()
    except TelegramConfigError as exc:
        note = f"telegram skipped: {exc}"
        if note not in ctx.state.concerns:
            logger.warning(note)
            ctx.state.concerns.append(note)
        return []
    return rows


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
    """Telegram gives jobs and gigs, the feeds give jobs, and the scouts both."""
    strategy, brief = _strategy(ctx), _brief(ctx)
    kinds = _searched(strategy, brief)
    rows = [s for s in _sources(ctx) if s.kind not in {WEB_SEARCH, TelegramAdapter.name}]
    scouted: list[SourcedListing] = []
    widest = max(_scope_days(brief, k) for k in kinds) if kinds else 0
    for row in _telegram(ctx) if kinds else []:
        posts = await TelegramPosts(ctx.steps).collect(
            ctx.adapters(row.kind, row.config),
            TelegramRequest(
                kinds=kinds,
                oldest=ctx.today - timedelta(days=widest),
                cursor=row.cursor,
            ),
        )
        scouted += posts.listings
        ctx.state.concerns += posts.concerns
        _save_cursors(ctx, posts.cursors)
    if rows and strategy.jobs and "job" in kinds:
        titles = strategy.jobs.titles
        keywords = [*titles, *(a for t in titles for a in strategy.title_aliases.get(t, []))]
        feeds = await JobFeeds([ctx.adapters(r.kind, r.config) for r in rows]).collect(
            FeedRequest(
                keywords=keywords,
                recency_days=_scope_days(brief, "job"),
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


def _searched(strategy: SeekerStrategy, brief: SeekerBrief) -> list[ListingKind]:
    return [k for k in KINDS if strategy.plan_for(k) and brief.scope(k)]


def _scope_days(brief: SeekerBrief, kind: ListingKind) -> int:
    scope = brief.scope(kind)
    return scope.recency_days if scope else 0


def _save_cursors(ctx: Ctx, cursors: dict[str, str | None]) -> None:
    with ctx.db.session() as session:
        rows = session.scalars(
            select(SourceConfig).where(SourceConfig.project_id == ctx.project_id)
        )
        for row in rows:
            if row.kind in cursors and cursors[row.kind] is not None:
                row.cursor = cursors[row.kind]


async def screen(ctx: Ctx) -> PhaseResult:
    screening = _search(ctx).screen(ctx.state.scouted, _strategy(ctx), _brief(ctx), ctx.today)
    ctx.state.kept = screening.kept
    ctx.state.excluded = screening.excluded[:MAX_EXCLUDED_KEPT]
    reasons = Counter(e.reason for e in screening.excluded)
    kept = Counter(listing.kind for listing in screening.kept)
    ctx.emit(
        "screened",
        {
            "kept": len(ctx.state.kept),
            "kept_by_kind": dict(kept),
            "over_limit": sum(screening.over_limit.values()),
            "excluded": dict(reasons),
        },
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
    """Basic enrichment: the employer or client and named people of the promising listings."""
    hidden = _board_names()
    with ctx.db.session() as session:
        open_items = _open(session, ctx.state.item_ids)
        promising = [i for i in open_items if (i.score_total or 0) >= IDENTIFY_MIN_SCORE]
        logger.info("identifying %d of %d open items", len(promising), len(open_items))
        tasks = [_listing_task(item, hidden) for item in promising]
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
    hidden = _board_names()
    with ctx.db.session() as session:
        open_items = _open(session, ctx.state.item_ids)[:top]
        tasks = [_field_task(item, spec, hidden) for item in open_items]
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
    payload = SeekerSummaryInput.build(_brief(ctx).preferences, _strategy(ctx), items)
    outcome = await SeekerRunSummarizer(ctx.steps).summarize(payload)
    ctx.state.summary = outcome.summary
    ctx.state.concerns += outcome.concerns
    patterns = len(outcome.summary.patterns) if outcome.summary else 0
    ctx.emit("summary", {"patterns": patterns})
    return None


def _open(session: Session, item_ids: list[int]) -> list[Item]:
    """Scored items no deal-breaker ruled out; researching the others is paid work nobody uses."""
    stored = [session.get(Item, i) for i in item_ids]
    found = [item for item in stored if item is not None]
    open_items = [i for i in found if not (i.score and ItemScore.model_validate(i.score).blockers)]
    if len(open_items) < len(found):
        logger.info("skipping %d items a deal-breaker ruled out", len(found) - len(open_items))
    return open_items


def _field_task(item: Item, spec: FieldSpec, hidden: frozenset[str]) -> FieldTask:
    found = item.details.get(IDENTITY)
    identity = LeadIdentity.model_validate(found) if found else None
    return field_task(item.id, _listing_task(item, hidden).lead, identity, spec)


def _board_names() -> frozenset[str]:
    return frozenset(b.name.casefold() for b in load_boards(BOARDS))


def _listing_task(item: Item, boards: frozenset[str]) -> LeadTask:
    """Gig boards that hide the client show their own name as the company, which names no one."""
    listing, _ = listing_of(item)
    hidden = (
        boards | {s.casefold() for s in listing.sources} if listing.kind == "gig" else frozenset()
    )
    lead = lead_input(listing, listing.summary, hidden)
    return LeadTask(item_id=item.id, listing=listing, lead=lead)


def listing_of(item: Item) -> tuple[Listing, ListingJudgment | None]:
    listing = Listing.model_validate(item.details[LISTING])
    judged = item.details.get(JUDGMENT)
    return listing, ListingJudgment.model_validate(judged) if judged else None


def _blocker(db: Database, project_id: int) -> str | None:
    return None if ProjectInputs.load(db, project_id).brief else "write the brief first"


SEEKER_SEARCH = FlowSpec[SeekerSearchState](
    pipeline="seeker.search",
    state_type=SeekerSearchState,
    phases=(
        Phase("plan", plan),
        Phase("approve_strategy", approve_strategy),
        Phase("boards", boards),
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
    blocker=_blocker,
)
