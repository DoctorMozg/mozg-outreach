"""Propose report fields as a new draft spec version; biz reviews its brief first."""

from pydantic import Field

from mozg_outreach.domain import ProjectMode
from mozg_outreach.pipelines.biz.discovery import BizDiscovery
from mozg_outreach.pipelines.biz.models import BizBrief, BriefReview
from mozg_outreach.pipelines.fields import FieldProposalInput, FieldProposer, SeekerWants
from mozg_outreach.pipelines.seeker.models import KINDS, SeekerBrief
from mozg_outreach.pipelines.seeker.search import COLUMNS
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


BUILT_IN = {ProjectMode.SEEKER: COLUMNS}


async def propose(ctx: Ctx) -> PhaseResult:
    inputs = ProjectInputs.load(ctx.db, ctx.project_id)
    raw = inputs.require_brief()
    brief: BizBrief | SeekerWants
    if inputs.mode == ProjectMode.BIZ:
        brief = BizBrief.model_validate(raw)
    else:
        # The CV isn't needed to propose fields, only what the seeker wants.
        seeker = SeekerBrief.model_validate(raw)
        kinds = [k for k in KINDS if seeker.scope(k)]
        brief = SeekerWants(preferences=seeker.preferences, kinds=kinds)
    proposal = await FieldProposer(ctx.steps).propose(
        FieldProposalInput(
            mode=inputs.mode,
            brief=brief,
            current_spec=inputs.spec,
            built_in=list(BUILT_IN.get(inputs.mode, ())),
        )
    )
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
SEEKER_FIELDS = FlowSpec[FieldProposalState](
    pipeline="seeker.fields", state_type=FieldProposalState, phases=(Phase("propose", propose),)
)
