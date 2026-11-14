"""Projects, their briefs, report field specs and data sources."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from mozg_outreach.api.schemas import (
    FieldSpecOut,
    ProjectIn,
    ProjectOut,
    ProjectPatch,
    RunOut,
    SourceIn,
    SourceKindOut,
    SourceOut,
    SourcePatch,
)
from mozg_outreach.api.services import Services, get_or_404, services
from mozg_outreach.domain import FieldSpec, ProjectMode
from mozg_outreach.pipelines.biz.models import BizBrief
from mozg_outreach.pipelines.freelance.models import FreelanceBrief
from mozg_outreach.pipelines.job.models import JobBrief
from mozg_outreach.runs.catalog import SEARCH_OPTIONS
from mozg_outreach.runs.fields import BIZ_FIELDS
from mozg_outreach.runs.job import WEB_SEARCH
from mozg_outreach.sources.factory import source_kinds
from mozg_outreach.storage.models import FieldSpecVersion, JsonObject, Project, Run, SourceConfig
from mozg_outreach.storage.repositories import FieldSpecRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
Svc = Annotated[Services, Depends(services)]

BRIEFS: dict[ProjectMode, type[BaseModel]] = {
    ProjectMode.BIZ: BizBrief,
    ProjectMode.JOB: JobBrief,
    ProjectMode.FREELANCE: FreelanceBrief,
}
# Feed adapters only serve job projects; biz and freelance search the web through agents.
SOURCE_NOTES = {
    WEB_SEARCH: "Agents search and read the web.",
    "telegram": "Reads public channels with your Telegram login; run telegram_login first.",
}


def _brief(mode: ProjectMode, brief: JsonObject | None) -> JsonObject | None:
    if brief is None:
        return None
    return BRIEFS[mode].model_validate(brief).model_dump(mode="json")


@router.get("/modes/{mode}/schemas")
async def mode_schemas(mode: ProjectMode) -> dict[str, JsonObject]:
    """JSON schemas the UI renders as forms: the brief and the search run's options."""
    return {
        "brief": BRIEFS[mode].model_json_schema(),
        "options": SEARCH_OPTIONS[mode].model_json_schema(),
    }


@router.get("/projects")
async def list_projects(svc: Svc) -> list[ProjectOut]:
    with svc.db.session() as session:
        rows = session.scalars(select(Project).order_by(Project.created_at.desc()))
        return [ProjectOut.model_validate(p) for p in rows]


@router.post("/projects", status_code=201)
async def create_project(body: ProjectIn, svc: Svc) -> ProjectOut:
    with svc.db.session() as session:
        project = Project(
            name=body.name,
            mode=body.mode,
            sender_name=body.sender_name,
            sender_tone=body.sender_tone,
            brief=_brief(body.mode, body.brief),
        )
        session.add(project)
        session.flush()
        # Every mode can search the web; feeds are opt-in.
        session.add(SourceConfig(project_id=project.id, kind=WEB_SEARCH))
        logger.info("created %s project %d", body.mode, project.id)
        return ProjectOut.model_validate(project)


@router.get("/projects/{project_id}")
async def get_project(project_id: int, svc: Svc) -> ProjectOut:
    with svc.db.session() as session:
        return ProjectOut.model_validate(get_or_404(session, Project, project_id))


@router.patch("/projects/{project_id}")
async def update_project(project_id: int, body: ProjectPatch, svc: Svc) -> ProjectOut:
    with svc.db.session() as session:
        project = get_or_404(session, Project, project_id)
        changes = body.model_dump(exclude_unset=True)
        if "brief" in changes:
            project.brief = _brief(project.mode, body.brief)
        for name in ("name", "sender_name", "sender_tone"):
            if name in changes:
                setattr(project, name, changes[name])
        return ProjectOut.model_validate(project)


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(project_id: int, svc: Svc) -> None:
    with svc.db.session() as session:
        busy = session.scalar(
            select(Run.id).where(Run.project_id == project_id, Run.finished_at.is_(None))
        )
        if busy is not None:
            raise HTTPException(409, f"run {busy} is still open; cancel it first")
        session.delete(get_or_404(session, Project, project_id))


@router.get("/projects/{project_id}/fields")
async def list_field_specs(project_id: int, svc: Svc) -> list[FieldSpecOut]:
    with svc.db.session() as session:
        get_or_404(session, Project, project_id)
        rows = session.scalars(
            select(FieldSpecVersion)
            .where(FieldSpecVersion.project_id == project_id)
            .order_by(FieldSpecVersion.version)
        )
        return [FieldSpecOut.model_validate(r) for r in rows]


@router.post("/projects/{project_id}/fields", status_code=201)
async def save_field_spec(project_id: int, spec: FieldSpec, svc: Svc) -> FieldSpecOut:
    """Store the user's edited spec as a new draft version; accepting it is a separate step."""
    with svc.db.session() as session:
        get_or_404(session, Project, project_id)
        return FieldSpecOut.model_validate(FieldSpecRepository(session).propose(project_id, spec))


@router.post("/projects/{project_id}/fields/{version}/accept")
async def accept_field_spec(project_id: int, version: int, svc: Svc) -> FieldSpecOut:
    with svc.db.session() as session:
        return FieldSpecOut.model_validate(FieldSpecRepository(session).accept(project_id, version))


@router.post("/projects/{project_id}/fields/propose", status_code=202)
async def propose_fields(project_id: int, svc: Svc) -> RunOut:
    """Start a run that reviews the brief and proposes report fields as a draft version."""
    with svc.db.session() as session:
        project = get_or_404(session, Project, project_id)
        if project.mode != ProjectMode.BIZ:
            raise HTTPException(409, f"{project.mode} reports have fixed columns")
        if not project.brief:
            raise HTTPException(409, "write the brief first")
    run_id = svc.runs.start(project_id, BIZ_FIELDS.pipeline, {})
    with svc.db.session() as session:
        return RunOut.model_validate(get_or_404(session, Run, run_id))


@router.get("/source-kinds")
async def list_source_kinds() -> list[SourceKindOut]:
    web = SourceKindOut(kind=WEB_SEARCH, modes=list(ProjectMode), note=SOURCE_NOTES[WEB_SEARCH])
    feeds = [
        SourceKindOut(kind=k, modes=[ProjectMode.JOB], note=SOURCE_NOTES.get(k, "Job feed."))
        for k in source_kinds()
    ]
    return [web, *feeds]


@router.get("/projects/{project_id}/sources")
async def list_sources(project_id: int, svc: Svc) -> list[SourceOut]:
    with svc.db.session() as session:
        get_or_404(session, Project, project_id)
        rows = session.scalars(select(SourceConfig).where(SourceConfig.project_id == project_id))
        return [SourceOut.model_validate(r) for r in rows]


def _check_source(svc: Services, mode: ProjectMode, kind: str, config: JsonObject) -> None:
    if kind == WEB_SEARCH:
        if config:
            raise HTTPException(422, "web_search takes no config")
        return
    if mode != ProjectMode.JOB:
        raise HTTPException(422, f"{kind} feeds serve job projects only")
    try:
        svc.adapters(kind, config)
    except ValueError as exc:  # unknown kind, or a config the adapter's model rejects
        raise HTTPException(422, str(exc)) from exc


@router.post("/projects/{project_id}/sources", status_code=201)
async def add_source(project_id: int, body: SourceIn, svc: Svc) -> SourceOut:
    with svc.db.session() as session:
        project = get_or_404(session, Project, project_id)
        _check_source(svc, project.mode, body.kind, body.config)
        row = SourceConfig(
            project_id=project_id, kind=body.kind, config=body.config, enabled=body.enabled
        )
        session.add(row)
        session.flush()
        return SourceOut.model_validate(row)


@router.patch("/sources/{source_id}")
async def update_source(source_id: int, body: SourcePatch, svc: Svc) -> SourceOut:
    with svc.db.session() as session:
        row = get_or_404(session, SourceConfig, source_id)
        if body.config is not None:
            project = get_or_404(session, Project, row.project_id)
            _check_source(svc, project.mode, row.kind, body.config)
            # A new config may cover other boards or channels, so the old position is void.
            row.config, row.cursor = body.config, None
        if body.enabled is not None:
            row.enabled = body.enabled
        return SourceOut.model_validate(row)


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(source_id: int, svc: Svc) -> None:
    with svc.db.session() as session:
        session.delete(get_or_404(session, SourceConfig, source_id))
