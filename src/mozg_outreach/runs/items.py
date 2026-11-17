"""Work on one item: enrich it, or draft its letters, cover letter or proposal."""

import logging
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.apply.models import FillReport
from mozg_outreach.cvs.library import CvLibrary
from mozg_outreach.cvs.models import CV_CHOICE, CvChoice
from mozg_outreach.cvs.picker import CvPicker
from mozg_outreach.domain import ItemKind
from mozg_outreach.letters.composer import WrittenLetter
from mozg_outreach.letters.models import Revision, RevisionTarget, Skipped
from mozg_outreach.naturalize.naturalizer import Naturalizer
from mozg_outreach.pipelines.biz.approach import SequenceClosedError
from mozg_outreach.pipelines.biz.models import Strategy
from mozg_outreach.pipelines.biz.outreach import BizOutreach
from mozg_outreach.pipelines.leads.enrichment import IDENTITY
from mozg_outreach.pipelines.leads.ingest import LeadRegistryIngest
from mozg_outreach.pipelines.leads.models import LeadIdentity
from mozg_outreach.pipelines.seeker.contacts import JobContactFinder
from mozg_outreach.pipelines.seeker.letters import CoverLetterRequest, JobLetterWriter
from mozg_outreach.pipelines.seeker.models import (
    JobContacts,
    Listing,
    SeekerBrief,
    SeekerStrategy,
)
from mozg_outreach.pipelines.seeker.proposals import ProposalRequest, ProposalWriter
from mozg_outreach.pipelines.seeker.screening import listing_text
from mozg_outreach.pipelines.seeker.skills import SkillMatcher
from mozg_outreach.registry.ingest import IngestContext
from mozg_outreach.runs.auto_enrich import ENRICH_PIPELINES
from mozg_outreach.runs.flow import FlowContext, FlowSpec, Phase, PhaseResult, RunState
from mozg_outreach.runs.inputs import ProjectInputs, load_voice, search_state
from mozg_outreach.runs.seeker import CONTACTS, listing_of
from mozg_outreach.storage.models import ENRICHED_AT, Draft, Item, Project, utcnow
from mozg_outreach.storage.repositories import DraftRepository, ItemRepository, NewDraft


class RevisionAsk(BaseModel):
    """Rewrite one draft with the user's suggestion instead of writing every letter afresh."""

    draft_id: int
    suggestion: str = Field(min_length=1)


class ItemState(RunState):
    item_id: int
    revision: RevisionAsk | None = None
    application: FillReport | None = None


logger = logging.getLogger(__name__)

type Ctx = FlowContext[ItemState]


def _item(ctx: Ctx) -> tuple[Item, dict[str, object]]:
    return search_state(ctx.db, ctx.state.item_id)


def _row(session: Session, item_id: int) -> Item:
    item = session.get(Item, item_id)
    if item is None:
        raise LookupError(f"item {item_id} was deleted during the run")
    return item


def _revision(ctx: Ctx) -> RevisionTarget | None:
    ask = ctx.state.revision
    if ask is None:
        return None
    with ctx.db.session() as session:
        draft = session.get(Draft, ask.draft_id)
        if draft is None or draft.item_id != ctx.state.item_id:
            raise LookupError(f"draft {ask.draft_id} of item {ctx.state.item_id} was deleted")
        # Validating the stored channel string checks it is still a known one.
        return RevisionTarget.model_validate(
            {
                "recipient": draft.recipient,
                "channel": draft.channel,
                "step": draft.step,
                "revision": Revision(previous_text=draft.body, suggestion=ask.suggestion),
            }
        )


def _store(ctx: Ctx, letter: WrittenLetter | Skipped) -> None:
    if isinstance(letter, Skipped):
        ctx.state.concerns.append(f"No letter for {letter.contact}: {letter.reason}")
        ctx.emit("skipped", letter.model_dump(mode="json"))
        return
    with ctx.db.session() as session:
        draft = DraftRepository(session).add(
            ctx.state.item_id,
            NewDraft(
                channel=letter.channel,
                recipient=letter.recipient,
                step=letter.step,
                subject=letter.subject,
                body=letter.text,
                review=letter.review,
            ),
        )
    # Emitting writes an event row, which must wait until this session releases its write lock.
    ctx.emit("draft", {"draft_id": draft.id, "recipient": letter.recipient})


async def biz_enrich(ctx: Ctx) -> PhaseResult:
    _, state = _item(ctx)
    strategy = Strategy.model_validate(state["strategy"])
    with ctx.db.session() as session:
        item = _row(session, ctx.state.item_id)
        dossier = await BizOutreach(
            session, ctx.steps, sites=ctx.sites, profiles=ctx.profiles
        ).enrich(item, strategy, ctx.run_id)
    ctx.state.concerns += dossier.concerns
    return None


async def biz_approach(ctx: Ctx) -> PhaseResult:
    _, state = _item(ctx)
    strategy = Strategy.model_validate(state["strategy"])
    with ctx.db.session() as session:
        item = _row(session, ctx.state.item_id)
        approach = await BizOutreach(session, ctx.steps).plan_approach(item, strategy)
    ctx.state.concerns += approach.plan.concerns
    ctx.emit("approach", {"touches": len(approach.plan.touches)})
    return None


async def biz_enrich_approach(ctx: Ctx) -> PhaseResult:
    """Inside enrichment a failed plan is a concern: letters then fall back to default routing."""
    try:
        return await biz_approach(ctx)
    except (AgentRunError, AgentOutputError) as exc:
        logger.warning("approach plan for item %d failed: %s", ctx.state.item_id, exc)
        ctx.state.concerns.append(f"No approach plan, so letters use the default routing: {exc}")
        return None
    except SequenceClosedError as exc:
        logger.info("item %d gets no new plan: %s", ctx.state.item_id, exc)
        ctx.state.concerns.append(f"No new approach plan: {exc}")
        return None


async def biz_enrich_letters(ctx: Ctx) -> PhaseResult:
    with ctx.db.session() as session:
        item = _row(session, ctx.state.item_id)
        project = session.get(Project, item.project_id)
        sender = project.sender_name if project else None
    if not sender:
        ctx.state.concerns.append(
            "No letters yet: set the sender name in the project settings, then Re-plan."
        )
        return None
    try:
        return await biz_letters(ctx)
    except SequenceClosedError as exc:
        ctx.state.concerns.append(f"No letters: {exc}")
        return None


async def biz_letters(ctx: Ctx) -> PhaseResult:
    _, state = _item(ctx)
    target = _revision(ctx)
    strategy = Strategy.model_validate(state["strategy"])
    with ctx.db.session() as session:
        item = _row(session, ctx.state.item_id)
        batch = await BizOutreach(session, ctx.steps).write_letters(item, strategy, target)
    for skipped in batch.skipped:
        ctx.state.concerns.append(f"No letter for {skipped.contact}: {skipped.reason}")
    ctx.emit("drafts", {"written": len(batch.letters), "skipped": len(batch.skipped)})
    return None


async def seeker_enrich(ctx: Ctx) -> PhaseResult:
    """Find who to write to about a job; a gig is answered on its own board."""
    item, _ = _item(ctx)
    listing, _ = listing_of(item)
    found = item.details.get(IDENTITY)
    identity = LeadIdentity.model_validate(found) if found else None
    finder = JobContactFinder(ctx.steps, ctx.sites, ctx.profiles)
    contacts = await finder.find(listing, identity)
    with ctx.db.session() as session:
        row = _row(session, ctx.state.item_id)
        ItemRepository(session).set_detail(row, CONTACTS, contacts)
        row.details = {**row.details, ENRICHED_AT: utcnow().isoformat()}
        context = IngestContext(project_id=row.project_id, run_id=ctx.run_id, item_id=row.id)
        LeadRegistryIngest(session).contacts(contacts, context)
    ctx.state.concerns += contacts.concerns
    return None


async def cv_text_for(ctx: Ctx, item: Item, listing: Listing) -> str | None:
    """The text of the CV to send: the lead's earlier pick, else a new one; None without CVs."""
    with ctx.db.session() as session:
        library = CvLibrary(session)
        texts = {cv.id: cv.text for cv in library.all()}
        options = library.options()
    stored = item.details.get(CV_CHOICE)
    choice = CvChoice.model_validate(stored) if stored else None
    if choice is not None and choice.cv_id in texts:
        return texts[choice.cv_id]
    if not options:
        if texts:
            ctx.state.concerns.append("No CV has a profile yet, so the brief's CV was used.")
        return None
    choice = await CvPicker(ctx.steps).pick(listing, options)
    with ctx.db.session() as session:
        ItemRepository(session).set_detail(_row(session, ctx.state.item_id), CV_CHOICE, choice)
    ctx.state.concerns += choice.concerns
    ctx.emit("cv", {"cv_id": choice.cv_id, "picked_by": choice.picked_by})
    return texts[choice.cv_id]


async def seeker_letter(ctx: Ctx) -> PhaseResult:
    """A cover letter for a job, a proposal for a gig."""
    item, state = _item(ctx)
    strategy = SeekerStrategy.model_validate(state["strategy"])
    listing, judgment = listing_of(item)
    inputs = ProjectInputs.load(ctx.db, ctx.project_id)
    cv_text = await cv_text_for(ctx, item, listing)
    background = SeekerBrief.model_validate(inputs.require_brief()).background(cv_text)
    skills = SkillMatcher(strategy).match(listing_text(listing))
    naturalizer = Naturalizer(ctx.steps)
    target = _revision(ctx)
    revision = target.revision if target else None
    if listing.kind == "gig":
        letter = await ProposalWriter(ctx.steps, naturalizer).write(
            ProposalRequest(
                gig=listing,
                skills=skills,
                rate=strategy.preferences.rate_floor,
                background=background,
                sender=inputs.sender(),
                voice=load_voice(ctx.db, "proposal"),
                revision=revision,
            )
        )
    else:
        found = item.details.get(CONTACTS)
        letter = await JobLetterWriter(ctx.steps, naturalizer).write(
            CoverLetterRequest(
                listing=listing,
                judgment=judgment,
                contacts=JobContacts.model_validate(found) if found else None,
                skills=skills,
                background=background,
                sender=inputs.sender(),
                voice=load_voice(ctx.db, "cover_letter"),
                revision=revision,
            )
        )
    _store(ctx, letter)
    return None


async def seeker_enrich_letter(ctx: Ctx) -> PhaseResult:
    """A job's enrichment ends with its cover letter, as a company's ends with its letters."""
    with ctx.db.session() as session:
        project = session.get(Project, ctx.project_id)
        sender = project.sender_name if project else None
    if not sender:
        ctx.state.concerns.append(
            "No cover letter yet: set the sender name in the project settings, then write it."
        )
        return None
    return await seeker_letter(ctx)


def _single(pipeline: str, phase: Phase[ItemState]) -> FlowSpec[ItemState]:
    return FlowSpec[ItemState](pipeline=pipeline, state_type=ItemState, phases=(phase,))


ITEM_FLOWS = (
    FlowSpec[ItemState](
        pipeline="biz.enrich",
        state_type=ItemState,
        phases=(
            Phase("enrich", biz_enrich),
            Phase("approach", biz_enrich_approach),
            Phase("letters", biz_enrich_letters),
        ),
    ),
    _single("biz.letters", Phase("letters", biz_letters)),
    # Re-planning rewrites the letters too, since each one is written for its step.
    FlowSpec[ItemState](
        pipeline="biz.approach",
        state_type=ItemState,
        phases=(Phase("approach", biz_approach), Phase("letters", biz_enrich_letters)),
    ),
    FlowSpec[ItemState](
        pipeline="seeker.enrich",
        state_type=ItemState,
        phases=(Phase("contacts", seeker_enrich), Phase("letter", seeker_enrich_letter)),
    ),
    _single("seeker.letters", Phase("letter", seeker_letter)),
)


ItemActionName = Literal["enrich", "letters", "approach", "apply"]


class ItemAction(BaseModel):
    """Which item pipelines exist per item kind, for the API to validate requests."""

    enrich: str | None
    letters: str
    approach: str | None = None
    apply: str | None = None

    def pipeline(self, action: ItemActionName) -> str | None:
        return {
            "enrich": self.enrich,
            "letters": self.letters,
            "approach": self.approach,
            "apply": self.apply,
        }[action]


ITEM_ACTIONS = {
    ItemKind.COMPANY: ItemAction(
        enrich=ENRICH_PIPELINES[ItemKind.COMPANY], letters="biz.letters", approach="biz.approach"
    ),
    ItemKind.JOB: ItemAction(
        enrich=ENRICH_PIPELINES[ItemKind.JOB], letters="seeker.letters", apply="seeker.apply"
    ),
    ItemKind.GIG: ItemAction(enrich=None, letters="seeker.letters"),
}
