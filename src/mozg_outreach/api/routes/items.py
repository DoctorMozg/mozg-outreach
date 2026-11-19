"""The report, selection, enrichment and letter runs, drafts and interaction history."""

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from mozg_outreach.api.routes.runs import ensure_idle, open_runs, run_out
from mozg_outreach.api.schemas import (
    ArchiveIn,
    DraftEdit,
    DraftOut,
    DraftRevisionIn,
    InteractionIn,
    InteractionOut,
    ItemDetail,
    ItemOut,
    LinkedEntity,
    MailThreadOut,
    ProfileLinkIn,
    RunOut,
    StepDoneIn,
)
from mozg_outreach.api.services import Services, get_or_404, services
from mozg_outreach.mail.edits import edited_share
from mozg_outreach.mail.suppression import Suppressions
from mozg_outreach.pipelines.biz.approach import FINAL_STOPS
from mozg_outreach.pipelines.biz.approach_models import ApproachStep
from mozg_outreach.pipelines.biz.outreach import APPROACH, link_profile
from mozg_outreach.pipelines.biz.progress import ApproachProgress, sequence_step
from mozg_outreach.runs.items import ITEM_ACTIONS, ItemActionName
from mozg_outreach.storage.models import (
    Draft,
    Entity,
    Interaction,
    Item,
    ItemLink,
    JsonObject,
    MailThread,
    Project,
)
from mozg_outreach.storage.repositories import (
    DraftRepository,
    InteractionRepository,
    ItemRepository,
    NewDraft,
    ReportFilter,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
Svc = Annotated[Services, Depends(services)]


class UserEdit(BaseModel):
    """Review notes on a draft the user rewrote by hand."""

    edited_by_user: bool = True
    based_on_version: int


def item_outs(session: Session, items: list[Item]) -> list[ItemOut]:
    """Rows for a list of leads: what the user last did with each, and its approach step."""
    interactions = InteractionRepository(session)
    activity = interactions.activity(items)
    planned = interactions.for_items([i.id for i in items if APPROACH in i.details])
    return [
        ItemOut.model_validate(i).model_copy(
            update={
                "activity": activity.get(i.id),
                "approach_step": sequence_step(i, planned.get(i.id, [])),
            }
        )
        for i in items
    ]


@router.get("/projects/{project_id}/items")
async def report(
    project_id: int, svc: Svc, view: Annotated[ReportFilter, Query()]
) -> list[ItemOut]:
    with svc.db.session() as session:
        get_or_404(session, Project, project_id)
        return item_outs(session, ItemRepository(session).report(project_id, view))


@router.get("/items/{item_id}")
async def get_item(item_id: int, svc: Svc) -> ItemDetail:
    with svc.db.session() as session:
        item = get_or_404(session, Item, item_id)
        linked = session.execute(
            select(ItemLink.role, Entity)
            .join(Entity, Entity.id == ItemLink.entity_id)
            .where(ItemLink.item_id == item_id)
        )
        return ItemDetail.model_validate(
            {
                **item_outs(session, [item])[0].model_dump(),
                "raw": item.raw,
                "details": item.details,
                "drafts": DraftRepository(session).for_item(item_id),
                "mail": _mail(session, item_id),
                "interactions": InteractionRepository(session).history(item_id),
                "entities": [
                    LinkedEntity(entity_id=e.id, kind=e.kind, name=e.name, role=role)
                    for role, e in linked
                ],
            }
        )


def _mail(session: Session, item_id: int) -> list[MailThreadOut]:
    found = []
    for row in session.scalars(select(MailThread).where(MailThread.item_id == item_id)):
        out = MailThreadOut.model_validate(row)
        draft = session.get(Draft, row.draft_id)
        if draft is not None and row.sent_body is not None:
            out = out.model_copy(update={"edited": edited_share(draft.body, row.sent_body)})
        found.append(out)
    return found


async def _start_item_run(
    svc: Services,
    item_id: int,
    action: ItemActionName,
    *,
    replace: bool = False,
    note: str | None = None,
) -> RunOut:
    """Starts the item's run; with `replace`, an open one of the same kind is cancelled first."""
    with svc.db.session() as session:
        item = get_or_404(session, Item, item_id)
        project = get_or_404(session, Project, item.project_id)
    pipeline = ITEM_ACTIONS[item.kind].pipeline(action)
    if pipeline is None:
        raise HTTPException(409, f"{item.kind} items have no {action} step")
    if replace:
        for run_id in open_runs(svc, project.id, pipeline, item_id):
            logger.info("item %d: run %d of %s replaced by a new one", item_id, run_id, pipeline)
            svc.runs.cancel(run_id)
            await svc.runs.wait(run_id)
    ensure_idle(svc, project.id, pipeline, item_id)
    state: dict[str, object] = {"item_id": item_id}
    if note:
        state["note"] = note
    return run_out(svc, svc.runs.start(project.id, pipeline, state))


@router.post("/items/{item_id}/enrich", status_code=202)
async def enrich_item(item_id: int, svc: Svc) -> RunOut:
    return await _start_item_run(svc, item_id, "enrich")


@router.post("/items/{item_id}/letters", status_code=202)
async def write_letters(item_id: int, svc: Svc) -> RunOut:
    return await _start_item_run(svc, item_id, "letters")


class ReplanIn(BaseModel):
    feedback: str | None = Field(default=None, max_length=2000)


@router.post("/items/{item_id}/approach", status_code=202)
async def plan_approach(item_id: int, svc: Svc, body: ReplanIn | None = None) -> RunOut:
    feedback = body.feedback.strip() if body and body.feedback else None
    return await _start_item_run(svc, item_id, "approach", note=feedback or None)


@router.post("/items/{item_id}/approach/steps/{step}/done", status_code=201)
async def mark_step_done(item_id: int, step: int, body: StepDoneIn, svc: Svc) -> ApproachStep:
    """The user made this touch; it is logged on the lead unless Gmail already logged it."""
    with svc.db.session() as session:
        item = _idle_company(svc, session, item_id)
        return ApproachProgress(session).mark_done(item, step, body.on or date.today())


@router.delete("/items/{item_id}/approach/steps/{step}/done")
async def undo_step_done(item_id: int, step: int, svc: Svc) -> ApproachStep:
    with svc.db.session() as session:
        item = _idle_company(svc, session, item_id)
        return ApproachProgress(session).undo(item, step)


def _idle_company(svc: Services, session: Session, item_id: int) -> Item:
    """A plan or enrichment run writes the stored approach at its end, which would undo a mark."""
    item = get_or_404(session, Item, item_id)
    for action in ("approach", "enrich"):
        pipeline = ITEM_ACTIONS[item.kind].pipeline(action)
        if pipeline:
            ensure_idle(svc, item.project_id, pipeline, item_id)
    return item


@router.post("/items/{item_id}/apply", status_code=202)
async def apply_to_job(item_id: int, svc: Svc) -> RunOut:
    """Fills the job's application form in a visible browser; the user reviews and submits.

    Filling again starts over: an application still open for the job is cancelled and its tab
    replaced, since the user asks for a fresh fill, not a second one beside it.
    """
    return await _start_item_run(svc, item_id, "apply", replace=True)


@router.post("/items/archive")
async def archive_items(body: ArchiveIn, svc: Svc) -> list[ItemOut]:
    """Archived leads leave the report; restoring brings them back with everything they had."""
    with svc.db.session() as session:
        try:
            items = ItemRepository(session).archive(body.item_ids, body.archived)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return item_outs(session, items)


@router.put("/items/{item_id}/people/linkedin", status_code=204)
async def set_linkedin(item_id: int, body: ProfileLinkIn, svc: Svc) -> None:
    with svc.db.session() as session:
        link_profile(session, get_or_404(session, Item, item_id), body.name, body.url)


@router.put("/drafts/{draft_id}", status_code=201)
async def edit_draft(draft_id: int, body: DraftEdit, svc: Svc) -> DraftOut:
    """A hand edit becomes the next version, so the generated text stays for comparison."""
    with svc.db.session() as session:
        base = get_or_404(session, Draft, draft_id)
        edited = DraftRepository(session).add(
            base.item_id,
            NewDraft(
                channel=base.channel,
                recipient=base.recipient,
                step=base.step,
                subject=body.subject,
                body=body.body,
                review=UserEdit(based_on_version=base.version),
            ),
        )
        return DraftOut.model_validate(edited)


@router.post("/drafts/{draft_id}/revise", status_code=202)
async def revise_draft(draft_id: int, body: DraftRevisionIn, svc: Svc) -> RunOut:
    """Rewrite one draft with the user's suggestion; the result is its next version."""
    with svc.db.session() as session:
        draft = get_or_404(session, Draft, draft_id)
        item = get_or_404(session, Item, draft.item_id)
        project_id, item_id = item.project_id, item.id
    pipeline = ITEM_ACTIONS[item.kind].letters
    ensure_idle(svc, project_id, pipeline, item_id)
    revision: JsonObject = {"draft_id": draft_id, "suggestion": body.suggestion}
    state: JsonObject = {"item_id": item_id, "revision": revision}
    return run_out(svc, svc.runs.start(project_id, pipeline, state))


@router.post("/items/{item_id}/interactions", status_code=201)
async def log_interaction(item_id: int, body: InteractionIn, svc: Svc) -> InteractionOut:
    with svc.db.session() as session:
        get_or_404(session, Item, item_id)
        row = InteractionRepository(session).log(item_id, body.kind, body.note)
        if body.happened_at is not None:
            row.happened_at = body.happened_at
        row.until = body.until
        if body.kind in FINAL_STOPS:
            Suppressions(session).add_lead(item_id, body.kind)
        return InteractionOut.model_validate(row)


@router.delete("/interactions/{interaction_id}", status_code=204)
async def delete_interaction(interaction_id: int, svc: Svc) -> None:
    with svc.db.session() as session:
        row = get_or_404(session, Interaction, interaction_id)
        if row.kind in FINAL_STOPS:
            Suppressions(session).lift(row.item_id, row.kind)
        session.delete(row)
