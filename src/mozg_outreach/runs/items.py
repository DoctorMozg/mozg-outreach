"""Work on one selected item: enrich it, or draft its letters, cover letter or proposal."""

from pydantic import BaseModel
from sqlalchemy.orm import Session

from mozg_outreach.letters.composer import WrittenLetter
from mozg_outreach.letters.models import Skipped
from mozg_outreach.naturalize.naturalizer import Naturalizer
from mozg_outreach.pipelines.biz.models import Strategy
from mozg_outreach.pipelines.biz.outreach import BizOutreach
from mozg_outreach.pipelines.freelance.models import FreelanceBrief, FreelanceStrategy
from mozg_outreach.pipelines.freelance.proposals import ProposalRequest, ProposalWriter
from mozg_outreach.pipelines.leads.ingest import LeadRegistryIngest
from mozg_outreach.pipelines.seeker.letters import CoverLetterRequest, JobLetterWriter
from mozg_outreach.pipelines.seeker.models import JobBrief, JobContacts, JobStrategy
from mozg_outreach.pipelines.seeker.screening import listing_text
from mozg_outreach.pipelines.seeker.search import JobSearch
from mozg_outreach.pipelines.seeker.skills import SkillMatcher
from mozg_outreach.registry.ingest import IngestContext
from mozg_outreach.runs.flow import FlowContext, FlowSpec, Phase, PhaseResult, RunState
from mozg_outreach.runs.freelance import gig_of
from mozg_outreach.runs.inputs import ProjectInputs, load_voice, search_state
from mozg_outreach.runs.job import CONTACTS, listing_of
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


async def job_enrich(ctx: Ctx) -> PhaseResult:
    item, _ = _item(ctx)
    listing, _ = listing_of(item)
    contacts = await JobSearch(ctx.steps, []).contacts(listing)
    with ctx.db.session() as session:
        row = _row(session, ctx.state.item_id)
        ItemRepository(session).set_detail(row, CONTACTS, contacts)
        context = IngestContext(project_id=row.project_id, run_id=ctx.run_id, item_id=row.id)
        LeadRegistryIngest(session).contacts(contacts, context)
    ctx.state.concerns += contacts.concerns
    return None


async def job_letter(ctx: Ctx) -> PhaseResult:
    item, state = _item(ctx)
    strategy = JobStrategy.model_validate(state["strategy"])
    listing, judgment = listing_of(item)
    found = item.details.get(CONTACTS)
    inputs = ProjectInputs.load(ctx.db, ctx.project_id)
    letter = await JobLetterWriter(ctx.steps, Naturalizer(ctx.steps)).write(
        CoverLetterRequest(
            listing=listing,
            judgment=judgment,
            contacts=JobContacts.model_validate(found) if found else None,
            skills=SkillMatcher(strategy).match(listing_text(listing)),
            cv_text=JobBrief.model_validate(inputs.require_brief()).cv_text,
            sender=inputs.sender(),
            voice=load_voice(ctx.db, "cover_letter"),
        )
    )
    _store(ctx, letter)
    return None


async def freelance_proposal(ctx: Ctx) -> PhaseResult:
    item, state = _item(ctx)
    strategy = FreelanceStrategy.model_validate(state["strategy"])
    gig, _ = gig_of(item)
    inputs = ProjectInputs.load(ctx.db, ctx.project_id)
    letter = await ProposalWriter(ctx.steps, Naturalizer(ctx.steps)).write(
        ProposalRequest(
            gig=gig,
            skills=SkillMatcher(strategy).match(listing_text(gig)),
            rate=strategy.preferences.rate_floor,
            cv_text=FreelanceBrief.model_validate(inputs.require_brief()).cv_text,
            sender=inputs.sender(),
            voice=load_voice(ctx.db, "proposal"),
        )
    )
    _store(ctx, letter)
    return None


def _single(pipeline: str, phase: Phase[ItemState]) -> FlowSpec[ItemState]:
    return FlowSpec[ItemState](pipeline=pipeline, state_type=ItemState, phases=(phase,))


ITEM_FLOWS = (
    _single("biz.enrich", Phase("enrich", biz_enrich)),
    _single("biz.letters", Phase("letters", biz_letters)),
    _single("job.enrich", Phase("contacts", job_enrich)),
    _single("job.letters", Phase("cover_letter", job_letter)),
    _single("freelance.letters", Phase("proposal", freelance_proposal)),
)


class ItemAction(BaseModel):
    """Which item pipelines exist per project mode, for the API to validate requests."""

    enrich: str | None
    letters: str


ITEM_ACTIONS = {
    "biz": ItemAction(enrich="biz.enrich", letters="biz.letters"),
    "job": ItemAction(enrich="job.enrich", letters="job.letters"),
    "freelance": ItemAction(enrich=None, letters="freelance.letters"),
}
