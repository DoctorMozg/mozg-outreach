"""The report, selection, enrichment and letter runs, drafts and interaction history."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from mozg_outreach.api.routes.runs import ensure_idle, run_out
from mozg_outreach.api.schemas import (
    DraftEdit,
    DraftOut,
    InteractionIn,
    InteractionOut,
    ItemDetail,
    ItemOut,
    ItemPatch,
    LinkedEntity,
    RunOut,
)
from mozg_outreach.api.services import Services, get_or_404, services
from mozg_outreach.runs.items import ITEM_ACTIONS
from mozg_outreach.storage.models import Draft, Entity, Interaction, Item, ItemLink, Project
from mozg_outreach.storage.repositories import (
    DraftRepository,
    InteractionRepository,
    ItemRepository,
    NewDraft,
)

router = APIRouter(prefix="/api")
Svc = Annotated[Services, Depends(services)]


class UserEdit(BaseModel):
    """Review notes on a draft the user rewrote by hand."""

    edited_by_user: bool = True
    based_on_version: int


@router.get("/projects/{project_id}/items")
async def report(project_id: int, svc: Svc) -> list[ItemOut]:
    with svc.db.session() as session:
        get_or_404(session, Project, project_id)
        return [ItemOut.model_validate(i) for i in ItemRepository(session).report(project_id)]


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
                **ItemOut.model_validate(item).model_dump(),
                "raw": item.raw,
                "details": item.details,
                "drafts": DraftRepository(session).for_item(item_id),
                "interactions": InteractionRepository(session).history(item_id),
                "entities": [
                    LinkedEntity(entity_id=e.id, kind=e.kind, name=e.name, role=role)
                    for role, e in linked
                ],
            }
        )


@router.patch("/items/{item_id}")
async def select_item(item_id: int, body: ItemPatch, svc: Svc) -> ItemOut:
    with svc.db.session() as session:
        item = get_or_404(session, Item, item_id)
        item.selected = body.selected
        return ItemOut.model_validate(item)


def _start_item_run(svc: Services, item_id: int, action: str) -> RunOut:
    with svc.db.session() as session:
        item = get_or_404(session, Item, item_id)
        project = get_or_404(session, Project, item.project_id)
        if not item.selected:
            raise HTTPException(409, f"item {item_id} is not selected")
    actions = ITEM_ACTIONS[project.mode]
    pipeline = actions.enrich if action == "enrich" else actions.letters
    if pipeline is None:
        raise HTTPException(409, f"{project.mode} items have nothing to enrich")
    ensure_idle(svc, project.id, pipeline)
    return run_out(svc, svc.runs.start(project.id, pipeline, {"item_id": item_id}))


@router.post("/items/{item_id}/enrich", status_code=202)
async def enrich_item(item_id: int, svc: Svc) -> RunOut:
    return _start_item_run(svc, item_id, "enrich")


@router.post("/items/{item_id}/letters", status_code=202)
async def write_letters(item_id: int, svc: Svc) -> RunOut:
    return _start_item_run(svc, item_id, "letters")


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
                subject=body.subject,
                body=body.body,
                review=UserEdit(based_on_version=base.version),
            ),
        )
        return DraftOut.model_validate(edited)


@router.post("/items/{item_id}/interactions", status_code=201)
async def log_interaction(item_id: int, body: InteractionIn, svc: Svc) -> InteractionOut:
    with svc.db.session() as session:
        get_or_404(session, Item, item_id)
        row = InteractionRepository(session).log(item_id, body.kind, body.note)
        if body.happened_at is not None:
            row.happened_at = body.happened_at
        return InteractionOut.model_validate(row)


@router.delete("/interactions/{interaction_id}", status_code=204)
async def delete_interaction(interaction_id: int, svc: Svc) -> None:
    with svc.db.session() as session:
        session.delete(get_or_404(session, Interaction, interaction_id))
