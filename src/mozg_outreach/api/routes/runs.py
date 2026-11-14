"""Start, watch, gate, cancel and resume runs; events stream over SSE with replay."""

import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sse_starlette import EventSourceResponse, ServerSentEvent

from mozg_outreach.api.schemas import RunEventOut, RunOut, RunStart
from mozg_outreach.api.services import Services, get_or_404, services
from mozg_outreach.runs.catalog import SEARCH_PIPELINES
from mozg_outreach.runs.flow import GateAnswer
from mozg_outreach.runs.manager import FINISHED
from mozg_outreach.storage.models import Project, Run

router = APIRouter(prefix="/api")
Svc = Annotated[Services, Depends(services)]
# Listeners wake on every append; the timeout only bounds how long a finished check waits.
WAKE_SECONDS = 15.0


def run_out(svc: Services, run_id: int) -> RunOut:
    with svc.db.session() as session:
        return RunOut.model_validate(get_or_404(session, Run, run_id))


def ensure_idle(svc: Services, project_id: int, pipeline: str, item_id: int | None = None) -> None:
    """Two runs on the same items would race, so a second waits; other items run in parallel."""
    with svc.db.session() as session:
        open_runs = session.scalars(
            select(Run).where(
                Run.project_id == project_id, Run.pipeline == pipeline, Run.finished_at.is_(None)
            )
        )
        clash = next((r.id for r in open_runs if r.state.get("item_id") == item_id), None)
    if clash is not None:
        raise HTTPException(409, f"run {clash} of {pipeline} is still open")


@router.post("/projects/{project_id}/runs", status_code=202)
async def start_search(project_id: int, body: RunStart, svc: Svc) -> RunOut:
    with svc.db.session() as session:
        project = get_or_404(session, Project, project_id)
        if not project.brief:
            raise HTTPException(409, "write the brief first")
        pipeline = SEARCH_PIPELINES[project.mode]
    ensure_idle(svc, project_id, pipeline)
    return run_out(svc, svc.runs.start(project_id, pipeline, {"options": body.options}))


@router.get("/projects/{project_id}/runs")
async def list_runs(project_id: int, svc: Svc) -> list[RunOut]:
    with svc.db.session() as session:
        rows = session.scalars(
            select(Run).where(Run.project_id == project_id).order_by(Run.id.desc())
        )
        return [RunOut.model_validate(r) for r in rows]


@router.get("/runs/{run_id}")
async def get_run(run_id: int, svc: Svc) -> RunOut:
    return run_out(svc, run_id)


@router.post("/runs/{run_id}/gate")
async def answer_gate(run_id: int, answer: GateAnswer, svc: Svc) -> RunOut:
    svc.runs.answer_gate(run_id, answer)
    return run_out(svc, run_id)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: int, svc: Svc) -> RunOut:
    svc.runs.cancel(run_id)
    await svc.runs.wait(run_id)
    return run_out(svc, run_id)


@router.post("/runs/{run_id}/resume", status_code=202)
async def resume_run(run_id: int, svc: Svc) -> RunOut:
    svc.runs.resume(run_id)
    return run_out(svc, run_id)


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: int,
    svc: Svc,
    last_event_id: Annotated[int | None, Header()] = None,
    after: int = 0,
) -> EventSourceResponse:
    """Replay events after `Last-Event-ID` (or `after`), then follow until the run ends."""
    run_out(svc, run_id)  # 404 before the stream opens
    start = last_event_id if last_event_id is not None else after

    async def stream() -> AsyncIterator[ServerSentEvent]:
        seq = start
        while True:
            signal = svc.runs.log.signal(run_id)
            # Status before events: a run's end and its last event are written together.
            finished = run_out(svc, run_id).status in FINISHED
            for event in svc.runs.log.after(run_id, seq):
                seq = event.seq
                body = RunEventOut.model_validate(event).model_dump(mode="json")
                yield ServerSentEvent(data=json.dumps(body), event=event.kind, id=str(seq))
            if finished:
                return
            await svc.runs.log.wait(signal, WAKE_SECONDS)

    return EventSourceResponse(stream())
