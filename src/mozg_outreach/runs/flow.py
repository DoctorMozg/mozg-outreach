"""A flow is an ordered list of phases; a phase may pause the run at a gate."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from mozg_outreach.apply.desk import ApplyDesk
from mozg_outreach.pipelines.biz.profile_search import ProfileFinder
from mozg_outreach.pipelines.biz.site_contacts import SiteReader
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.runs.events import RunLog
from mozg_outreach.sources.base import SourceAdapter
from mozg_outreach.storage.db import Database
from mozg_outreach.storage.models import JsonObject

# Builds a feed adapter from a project's source row (kind, config).
type AdapterFactory = Callable[[str, JsonObject], SourceAdapter]


class GateAnswer(BaseModel):
    approve: bool
    feedback: str | None = Field(default=None, description="Why not, when not approved.")
    revised: JsonObject | None = Field(default=None, description="The user's edited version.")


class GateRequest(BaseModel):
    kind: str
    payload: JsonObject


class RunState(BaseModel):
    """What every flow keeps between phases."""

    answers: dict[str, GateAnswer] = Field(default_factory=dict)
    feedback: str | None = None
    concerns: list[str] = Field(default_factory=list)


@dataclass
class FlowContext[TState: RunState]:
    run_id: int
    project_id: int
    state: TState
    steps: PipelineSteps
    db: Database
    log: RunLog
    today: date
    workspace: Path
    adapters: AdapterFactory
    applications: ApplyDesk | None = None
    sites: SiteReader | None = None
    profiles: ProfileFinder | None = None
    # Answers this run's own gate from outside the run task, such as a detected confirmation page.
    answer_gate: Callable[[GateAnswer], None] | None = None

    def emit(self, kind: str, payload: JsonObject) -> None:
        self.log.append(self.run_id, kind, payload)


# A phase returns None to go on, a phase name to jump to, or a gate to pause at.
type PhaseResult = None | str | GateRequest
type PhaseFn[TState: RunState] = Callable[[FlowContext[TState]], Awaitable[PhaseResult]]


@dataclass(frozen=True)
class Phase[TState: RunState]:
    name: str
    run: PhaseFn[TState]


@dataclass(frozen=True)
class FlowSpec[TState: RunState]:
    pipeline: str
    state_type: type[TState]
    phases: tuple[Phase[TState], ...]
    # Says why the project can't run this flow yet; checked on start and resume, before any spend.
    blocker: Callable[[Database, int], str | None] | None = None

    def index(self, name: str | None) -> int:
        if name is None:
            return 0
        for i, phase in enumerate(self.phases):
            if phase.name == name:
                return i
        raise LookupError(f"{self.pipeline} has no phase {name!r}")


@dataclass(frozen=True)
class Approval[TState: RunState]:
    """A gate that shows a draft from the state, takes edits, or sends the run back with notes."""

    kind: str
    back_to: str
    field: str
    model: type[BaseModel]

    def check(self, revised: JsonObject) -> BaseModel:
        return self.model.model_validate(revised)

    async def __call__(self, ctx: FlowContext[TState]) -> PhaseResult:
        answer = ctx.state.answers.pop(self.kind, None)
        if answer is None:
            draft = getattr(ctx.state, self.field)
            shown = draft.model_dump(mode="json") if isinstance(draft, BaseModel) else None
            return GateRequest(kind=self.kind, payload={self.field: shown})
        if answer.approve:
            if answer.revised is not None:
                setattr(ctx.state, self.field, self.check(answer.revised))
            ctx.state.feedback = None
            return None
        ctx.state.feedback = answer.feedback or "Rejected without notes; take a different approach."
        return self.back_to


# Flows differ in their state type, and the manager only round-trips that state as JSON.
type AnyFlow = FlowSpec[Any]
