"""Review a biz brief, then propose report fields as a new draft spec version."""

from pydantic import Field

from mozg_outreach.pipelines.biz.discovery import BizDiscovery
from mozg_outreach.pipelines.biz.models import BizBrief, BriefReview
from mozg_outreach.runs.flow import FlowContext, FlowSpec, Phase, PhaseResult, RunState
from mozg_outreach.runs.inputs import ProjectInputs
from mozg_outreach.storage.repositories import FieldSpecRepository


class FieldProposalState(RunState):
    review: BriefReview | None = None
    rationale: str | None = None
    version: int | None = Field(default=None, description="The draft spec version proposed.")


type Ctx = FlowContext[FieldProposalState]


async def review(ctx: Ctx) -> PhaseResult:
    brief = BizBrief.model_validate(ProjectInputs.load(ctx.db, ctx.project_id).require_brief())
    ctx.state.review = await BizDiscovery(ctx.steps).review_brief(brief)
    ctx.emit("brief_review", ctx.state.review.model_dump(mode="json"))
    return None


async def propose(ctx: Ctx) -> PhaseResult:
    inputs = ProjectInputs.load(ctx.db, ctx.project_id)
    brief = BizBrief.model_validate(inputs.require_brief())
    proposal = await BizDiscovery(ctx.steps).propose_fields(brief, inputs.spec)
    with ctx.db.session() as session:
        ctx.state.version = (
            FieldSpecRepository(session).propose(ctx.project_id, proposal.spec).version
        )
    ctx.state.rationale = proposal.rationale
    ctx.emit("fields_proposed", {"version": ctx.state.version})
    return None


BIZ_FIELDS = FlowSpec[FieldProposalState](
    pipeline="biz.fields",
    state_type=FieldProposalState,
    phases=(Phase("review", review), Phase("propose", propose)),
)
