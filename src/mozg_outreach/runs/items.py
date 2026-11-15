"""Work on one selected item: enrich it, or draft its letters, cover letter or proposal."""

from pydantic import BaseModel
from sqlalchemy.orm import Session

from mozg_outreach.cvs.library import CvLibrary
from mozg_outreach.cvs.models import CV_CHOICE, CvChoice
from mozg_outreach.cvs.picker import CvPicker
from mozg_outreach.domain import ItemKind
from mozg_outreach.letters.composer import WrittenLetter
from mozg_outreach.letters.models import Skipped
from mozg_outreach.naturalize.naturalizer import Naturalizer
from mozg_outreach.pipelines.biz.models import Strategy
from mozg_outreach.pipelines.biz.outreach import BizOutreach
from mozg_outreach.pipelines.leads.ingest import LeadRegistryIngest
from mozg_outreach.pipelines.seeker.letters import CoverLetterRequest, JobLetterWriter
from mozg_outreach.pipelines.seeker.models import (
    JobContacts,
    Listing,
    SeekerBrief,
    SeekerStrategy,
)
from mozg_outreach.pipelines.seeker.proposals import ProposalRequest, ProposalWriter
from mozg_outreach.pipelines.seeker.screening import listing_text
from mozg_outreach.pipelines.seeker.search import SeekerSearch
from mozg_outreach.pipelines.seeker.skills import SkillMatcher
from mozg_outreach.registry.ingest import IngestContext
from mozg_outreach.runs.flow import FlowContext, FlowSpec, Phase, PhaseResult, RunState
from mozg_outreach.runs.inputs import ProjectInputs, load_voice, search_state
from mozg_outreach.runs.seeker import CONTACTS, listing_of
from mozg_outreach.storage.models import Item
from mozg_outreach.storage.repositories import DraftRepository, ItemRepository, NewDraft


class ItemState(RunState):
    item_id: int


type Ctx = FlowContext[ItemState]


def _item(ctx: Ctx) -> tuple[Item, dict[str, object]]:
    return search_state(ctx.db, ctx.state.item_id)


def _row(session: Session, item_id: int) -> Item:
    item = session.get(Item, item_id)
    if item is None:
        raise LookupError(f"item {item_id} was deleted during the run")
    return item


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
        dossier = await BizOutreach(session, ctx.steps).enrich(item, strategy, ctx.run_id)
    ctx.state.concerns += dossier.concerns
    return None


async def biz_letters(ctx: Ctx) -> PhaseResult:
    _, state = _item(ctx)
    strategy = Strategy.model_validate(state["strategy"])
    with ctx.db.session() as session:
        item = _row(session, ctx.state.item_id)
        batch = await BizOutreach(session, ctx.steps).write_letters(item, strategy)
    for skipped in batch.skipped:
        ctx.state.concerns.append(f"No letter for {skipped.contact}: {skipped.reason}")
    ctx.emit("drafts", {"written": len(batch.letters), "skipped": len(batch.skipped)})
    return None


async def seeker_enrich(ctx: Ctx) -> PhaseResult:
    """Find who to write to about a job; a gig is answered on its own board."""
    item, _ = _item(ctx)
    listing, _ = listing_of(item)
    contacts = await SeekerSearch(ctx.steps, []).contacts(listing)
    with ctx.db.session() as session:
        row = _row(session, ctx.state.item_id)
        ItemRepository(session).set_detail(row, CONTACTS, contacts)
        context = IngestContext(project_id=row.project_id, run_id=ctx.run_id, item_id=row.id)
        LeadRegistryIngest(session).contacts(contacts, context)
    ctx.state.concerns += contacts.concerns
    return None


async def _cv_text(ctx: Ctx, item: Item, listing: Listing) -> str | None:
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
    cv_text = await _cv_text(ctx, item, listing)
    background = SeekerBrief.model_validate(inputs.require_brief()).background(cv_text)
    skills = SkillMatcher(strategy).match(listing_text(listing))
    naturalizer = Naturalizer(ctx.steps)
    if listing.kind == "gig":
        letter = await ProposalWriter(ctx.steps, naturalizer).write(
            ProposalRequest(
                gig=listing,
                skills=skills,
                rate=strategy.preferences.rate_floor,
                background=background,
                sender=inputs.sender(),
                voice=load_voice(ctx.db, "proposal"),
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
            )
        )
    _store(ctx, letter)
    return None


def _single(pipeline: str, phase: Phase[ItemState]) -> FlowSpec[ItemState]:
    return FlowSpec[ItemState](pipeline=pipeline, state_type=ItemState, phases=(phase,))


ITEM_FLOWS = (
    _single("biz.enrich", Phase("enrich", biz_enrich)),
    _single("biz.letters", Phase("letters", biz_letters)),
    _single("seeker.enrich", Phase("contacts", seeker_enrich)),
    _single("seeker.letters", Phase("letter", seeker_letter)),
)


class ItemAction(BaseModel):
    """Which item pipelines exist per item kind, for the API to validate requests."""

    enrich: str | None
    letters: str


ITEM_ACTIONS = {
    ItemKind.COMPANY: ItemAction(enrich="biz.enrich", letters="biz.letters"),
    ItemKind.JOB: ItemAction(enrich="seeker.enrich", letters="seeker.letters"),
    ItemKind.GIG: ItemAction(enrich=None, letters="seeker.letters"),
}
