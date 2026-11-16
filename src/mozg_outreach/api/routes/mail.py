"""Email drafts in the user's Gmail: push one, check what became of them."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from mozg_outreach.api.schemas import MailStatusOut, MailThreadOut
from mozg_outreach.api.services import Services, services
from mozg_outreach.mail.drafts import CheckReport, GmailDrafts
from mozg_outreach.mail.models import ADDRESS_VAR, PASSWORD_VAR
from mozg_outreach.storage.models import MailThread

router = APIRouter(prefix="/api")
Svc = Annotated[Services, Depends(services)]


def _gmail(svc: Services) -> GmailDrafts:
    if svc.gmail is None:
        raise HTTPException(409, f"Gmail is not set up: set {ADDRESS_VAR} and {PASSWORD_VAR}")
    return svc.gmail


@router.get("/mail")
async def mail_status(svc: Svc) -> MailStatusOut:
    return MailStatusOut(address=svc.gmail.address if svc.gmail else None)


@router.post("/drafts/{draft_id}/gmail", status_code=201)
async def push_draft(draft_id: int, svc: Svc) -> MailThreadOut:
    """Puts this version of an email letter into Gmail Drafts; the user sends it from Gmail."""
    thread_id = await _gmail(svc).push(draft_id)
    with svc.db.session() as session:
        return MailThreadOut.model_validate(session.get_one(MailThread, thread_id))


@router.post("/mail/check")
async def check_mail(svc: Svc) -> CheckReport:
    return await _gmail(svc).check()
