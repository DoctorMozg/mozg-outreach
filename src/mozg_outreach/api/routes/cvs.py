"""The user's PDF CVs and the CV picked for each job or gig."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.api.schemas import CvOut, CvPatch, CvPick, CvUploadOut
from mozg_outreach.api.services import Services, get_or_404, services
from mozg_outreach.cvs.library import MAX_CVS, CvLibrary
from mozg_outreach.cvs.models import CV_CHOICE, CvChoice
from mozg_outreach.cvs.pdf import MAX_PDF_BYTES, CvFileError, read_pdf
from mozg_outreach.cvs.picker import CvProfiler
from mozg_outreach.domain import ItemKind
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.storage.models import Cv, Item
from mozg_outreach.storage.repositories import ItemRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
Svc = Annotated[Services, Depends(services)]


def _profiler(svc: Services) -> tuple[CvProfiler, PipelineSteps]:
    steps = PipelineSteps(svc.runner, svc.prompts, svc.home / "workspaces" / "cvs")
    return CvProfiler(steps), steps


@router.get("/cvs")
async def list_cvs(svc: Svc) -> list[CvOut]:
    with svc.db.session() as session:
        return [CvOut.model_validate(cv) for cv in CvLibrary(session).all()]


@router.post("/cvs", status_code=201)
async def upload_cv(
    request: Request,
    svc: Svc,
    name: Annotated[str, Query(min_length=1, max_length=100)],
    filename: Annotated[str, Query(min_length=1, max_length=200)],
) -> CvUploadOut:
    """The body is the PDF itself. Profiling is one agent call; a failed one keeps the CV."""
    data = await request.body()
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(413, f"the PDF is {len(data) / 1e6:.1f} MB, over 2.5 MB")
    try:
        text = read_pdf(data)
    except CvFileError as exc:
        raise HTTPException(422, str(exc)) from exc
    with svc.db.session() as session:
        if len(CvLibrary(session).all()) >= MAX_CVS:
            raise HTTPException(409, f"keep at most {MAX_CVS} CVs; delete one first")
        cv = Cv(name=name, filename=filename, pdf=data, text=text, summary=None)
        session.add(cv)
        session.flush()
        cv_id = cv.id
    profiler, steps = _profiler(svc)
    concerns: list[str] = []
    try:
        draft = await profiler.profile(text)
    except (AgentRunError, AgentOutputError) as exc:
        logger.warning("profiling CV %d failed: %s", cv_id, exc)
        concerns.append(f"The CV was saved but not profiled: {exc}")
    else:
        concerns += draft.concerns
        with svc.db.session() as session:
            get_or_404(session, Cv, cv_id).summary = draft.summary.model_dump(mode="json")
    with svc.db.session() as session:
        out = CvOut.model_validate(get_or_404(session, Cv, cv_id))
    return CvUploadOut(cv=out, concerns=concerns, cost_usd=steps.cost_usd)


@router.post("/cvs/{cv_id}/profile")
async def profile_cv(cv_id: int, svc: Svc) -> CvOut:
    with svc.db.session() as session:
        text = get_or_404(session, Cv, cv_id).text
    profiler, _ = _profiler(svc)
    draft = await profiler.profile(text)
    with svc.db.session() as session:
        cv = get_or_404(session, Cv, cv_id)
        cv.summary = draft.summary.model_dump(mode="json")
        return CvOut.model_validate(cv)


@router.patch("/cvs/{cv_id}")
async def edit_cv(cv_id: int, body: CvPatch, svc: Svc) -> CvOut:
    with svc.db.session() as session:
        cv = get_or_404(session, Cv, cv_id)
        if body.name is not None:
            cv.name = body.name
        if body.summary is not None:
            cv.summary = body.summary.model_dump(mode="json")
        return CvOut.model_validate(cv)


@router.get("/cvs/{cv_id}/pdf")
async def download_cv(cv_id: int, svc: Svc) -> Response:
    with svc.db.session() as session:
        cv = get_or_404(session, Cv, cv_id)
        safe = cv.filename.replace('"', "")
        return Response(
            cv.pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{safe}"'},
        )


@router.delete("/cvs/{cv_id}", status_code=204)
async def delete_cv(cv_id: int, svc: Svc) -> None:
    """Leads that picked it pick again at their next letter."""
    with svc.db.session() as session:
        session.delete(get_or_404(session, Cv, cv_id))


@router.put("/items/{item_id}/cv")
async def pick_cv(item_id: int, body: CvPick, svc: Svc) -> CvChoice | None:
    """The user's pick replaces the ratings' pick; null clears it."""
    with svc.db.session() as session:
        item = get_or_404(session, Item, item_id)
        if item.kind == ItemKind.COMPANY:
            raise HTTPException(409, "company leads get letters, not a CV")
        if body.cv_id is None:
            ItemRepository(session).set_detail(item, CV_CHOICE, None)
            return None
        get_or_404(session, Cv, body.cv_id)
        stored = item.details.get(CV_CHOICE)
        earlier = CvChoice.model_validate(stored) if stored else None
        choice = CvChoice(
            cv_id=body.cv_id,
            picked_by="user",
            listing_language=earlier.listing_language if earlier else None,
            ranked=earlier.ranked if earlier else [],
            concerns=[],
        )
        ItemRepository(session).set_detail(item, CV_CHOICE, choice)
        return choice
