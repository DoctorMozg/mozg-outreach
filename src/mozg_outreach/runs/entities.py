"""Search again for one registry person or company, on the user's request."""

from pydantic import Field
from sqlalchemy.orm import Session

from mozg_outreach.pipelines.entities.research import EntityResearcher, EntityUpdate, research_input
from mozg_outreach.registry.ingest import IngestContext
from mozg_outreach.runs.flow import FlowContext, FlowSpec, Phase, PhaseResult, RunState
from mozg_outreach.storage.models import Entity

ENTITY_RESEARCH = "registry.research"


class EntityState(RunState):
    entity_id: int
    note: str | None = Field(default=None, description="The user's hint for this search.")


type Ctx = FlowContext[EntityState]


async def research(ctx: Ctx) -> PhaseResult:
    with ctx.db.session() as session:
        request = research_input(session, _entity(session, ctx), ctx.state.note)
    findings = await EntityResearcher(ctx.steps).find(request)
    # The agent ran for minutes; the entity may have been merged or deleted meanwhile.
    with ctx.db.session() as session:
        context = IngestContext(project_id=ctx.project_id, run_id=ctx.run_id, item_id=None)
        report = EntityUpdate(session).apply(_entity(session, ctx), findings, context)
    ctx.emit("entity", report.model_dump(mode="json"))
    ctx.state.concerns += report.notes + findings.concerns
    return None


def _entity(session: Session, ctx: Ctx) -> Entity:
    entity = session.get(Entity, ctx.state.entity_id)
    if entity is None:
        raise LookupError(f"entity {ctx.state.entity_id} was merged or deleted during the run")
    return entity


ENTITY_FLOWS = (
    FlowSpec[EntityState](
        pipeline=ENTITY_RESEARCH,
        state_type=EntityState,
        phases=(Phase("research", research),),
    ),
)
