"""Apply to a job: fill its form in the visible browser, then wait for the applicant to submit."""

import logging

from mozg_outreach.apply.answers import AgentAnswerer
from mozg_outreach.apply.filler import Application
from mozg_outreach.apply.form import FormError
from mozg_outreach.cvs.models import CV_CHOICE, CvChoice
from mozg_outreach.pipelines.seeker.models import JobContacts, Listing, SeekerBrief
from mozg_outreach.runs.flow import FlowSpec, GateAnswer, GateRequest, Phase, PhaseResult
from mozg_outreach.runs.inputs import ProjectInputs
from mozg_outreach.runs.items import Ctx, ItemState, cv_text_for
from mozg_outreach.runs.manager import RunStateError
from mozg_outreach.runs.seeker import CONTACTS, listing_of
from mozg_outreach.storage.db import Database
from mozg_outreach.storage.models import Cv, Item
from mozg_outreach.storage.repositories import DraftRepository, InteractionRepository

logger = logging.getLogger(__name__)

GATE = "application"
CONFIRMED = "Confirmation page: "


async def apply_fill(ctx: Ctx) -> PhaseResult:
    desk = ctx.applications
    if desk is None:
        raise RuntimeError("the application browser is not available in this server")
    with ctx.db.session() as session:
        item = session.get_one(Item, ctx.state.item_id)
        session.expunge(item)
    listing, _ = listing_of(item)
    if listing.kind != "job":
        raise ValueError(f"item {item.id} is a {listing.kind}; only jobs have application forms")
    brief = SeekerBrief.model_validate(ProjectInputs.load(ctx.db, ctx.project_id).require_brief())
    if brief.applicant is None:
        raise ValueError("add your applicant details to the brief first")
    cv_text, cv_pdf = await _cv(ctx, item, listing, brief)
    url = _form_url(item, listing)
    answer_gate = ctx.answer_gate

    def on_sent(page_url: str) -> None:
        if answer_gate is None:
            return
        try:
            answer_gate(GateAnswer(approve=True, feedback=f"{CONFIRMED}{page_url}"))
        except RunStateError:
            # The applicant answered first, or the run moved on; their answer stands.
            logger.info("item %d: confirmation seen after the gate closed", item.id)

    try:
        report = await desk.prepare(
            item.id,
            url,
            Application(
                profile=brief.applicant,
                job_title=listing.title,
                company=listing.company,
                job_summary=listing.summary,
                cv_text=cv_text,
                cv_pdf=cv_pdf,
                letter=_cover_letter(ctx.db, item.id),
            ),
            AgentAnswerer(ctx.steps),
            on_sent,
        )
    except FormError:
        await desk.close(item.id)
        raise
    ctx.state.application = report
    ctx.state.concerns += report.concerns
    ctx.emit("application", {"url": report.url, "filled": len(report.filled)})
    return None


async def apply_review(ctx: Ctx) -> PhaseResult:
    answer = ctx.state.answers.pop(GATE, None)
    report = ctx.state.application
    if answer is None:
        shown = report.model_dump(mode="json") if report else None
        return GateRequest(kind=GATE, payload={"application": shown})
    if answer.approve:
        note = answer.feedback or "Submitted by hand in the application browser."
        with ctx.db.session() as session:
            InteractionRepository(session).log(ctx.state.item_id, "sent", note)
        ctx.emit("sent", {"note": note})
    else:
        ctx.state.concerns.append(answer.feedback or "The application was not submitted.")
    if ctx.applications is not None:
        await ctx.applications.close(ctx.state.item_id)
    return None


def _form_url(item: Item, listing: Listing) -> str:
    found = item.details.get(CONTACTS)
    contacts = JobContacts.model_validate(found) if found else None
    return (contacts.apply_url if contacts else None) or listing.apply_url or listing.url


async def _cv(
    ctx: Ctx, item: Item, listing: Listing, brief: SeekerBrief
) -> tuple[str, bytes | None]:
    """The picked CV's text and PDF; the brief's CV text and no file when the library is empty."""
    text = await cv_text_for(ctx, item, listing)
    if text is None:
        return brief.cv_text, None
    with ctx.db.session() as session:
        stored = session.get_one(Item, item.id).details.get(CV_CHOICE)
        cv = session.get(Cv, CvChoice.model_validate(stored).cv_id) if stored else None
        return text, cv.pdf if cv else None


def _cover_letter(db: Database, item_id: int) -> str | None:
    with db.session() as session:
        letters = [
            d for d in DraftRepository(session).for_item(item_id) if d.channel == "cover_letter"
        ]
        return max(letters, key=lambda d: d.created_at).body if letters else None


def _blocker(db: Database, project_id: int) -> str | None:
    brief = ProjectInputs.load(db, project_id).brief
    if brief is None:
        return "write the brief first"
    if SeekerBrief.model_validate(brief).applicant is None:
        return "add your applicant details (name and email) to the brief first"
    return None


SEEKER_APPLY = FlowSpec[ItemState](
    pipeline="seeker.apply",
    state_type=ItemState,
    phases=(Phase("fill", apply_fill), Phase("review", apply_review)),
    blocker=_blocker,
)
