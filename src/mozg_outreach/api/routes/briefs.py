"""AI-drafted briefs: from the user's company site for biz, from the CV for seeker."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.api.schemas import BriefDraftOut, BriefRequest
from mozg_outreach.api.services import Services, services
from mozg_outreach.domain import ProjectMode
from mozg_outreach.pipelines.briefs import BriefDraft, BriefWriter, FetchLog
from mozg_outreach.pipelines.fields import FieldProposer
from mozg_outreach.pipelines.seeker.models import SeekerBrief
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.runs.fields import proposal_input
from mozg_outreach.runs.inputs import ProjectInputs
from mozg_outreach.storage.repositories import FieldSpecRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects")
Svc = Annotated[Services, Depends(services)]


@router.post("/{project_id}/brief/generate")
async def generate_brief(project_id: int, body: BriefRequest, svc: Svc) -> BriefDraftOut:
    """Draft the brief and a field spec for it; two agent calls, so it takes a while."""
    inputs = ProjectInputs.load(svc.db, project_id)
    fetched = FetchLog()
    steps = PipelineSteps(
        svc.runner, svc.prompts, svc.home / "workspaces" / f"project-{project_id}", fetched
    )
    writer = BriefWriter(steps, fetched)
    if inputs.mode == ProjectMode.BIZ:
        if body.site_url is None:
            raise HTTPException(422, "a biz brief is written from the company site: send site_url")
        draft = await writer.biz(str(body.site_url), body.note)
    else:
        if body.cv_text is None:
            raise HTTPException(422, "a seeker brief is written from the CV: send cv_text")
        current = SeekerBrief.model_validate(inputs.brief) if inputs.brief else None
        draft = await writer.seeker(body.cv_text, body.note, current)
    version, concerns = await _draft_fields(svc, steps, inputs, draft)
    logger.info("brief draft for project %d cost $%.2f", project_id, steps.cost_usd)
    return BriefDraftOut(
        brief=draft.brief.model_dump(mode="json"),
        concerns=[*draft.concerns, *concerns],
        field_version=version,
        cost_usd=steps.cost_usd,
    )


async def _draft_fields(
    svc: Services, steps: PipelineSteps, inputs: ProjectInputs, draft: BriefDraft
) -> tuple[int | None, list[str]]:
    """A failed proposal still returns the brief, which cost more and is what was asked for."""
    request = proposal_input(inputs.mode, draft.brief, inputs.spec)
    try:
        proposal = await FieldProposer(steps).propose(request)
    except (AgentRunError, AgentOutputError) as exc:
        logger.warning("field proposal for project %d failed: %s", inputs.project_id, exc)
        return None, [f"Report fields were not drafted: {exc}"]
    with svc.db.session() as session:
        version = FieldSpecRepository(session).propose(inputs.project_id, proposal.spec).version
    return version, []
