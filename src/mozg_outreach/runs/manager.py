"""Start, resume, cancel and gate runs; each run is one asyncio task behind a semaphore."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mozg_outreach.agents import AgentEvent, PromptLibrary, StepRunner
from mozg_outreach.apply.desk import ApplyDesk
from mozg_outreach.domain import RunStatus
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.runs.events import RunLog
from mozg_outreach.runs.flow import (
    AdapterFactory,
    AnyFlow,
    Approval,
    FlowContext,
    GateAnswer,
    GateRequest,
    RunState,
)
from mozg_outreach.storage.db import Database
from mozg_outreach.storage.models import JsonObject, Run, utcnow

logger = logging.getLogger(__name__)

FINISHED = frozenset({RunStatus.COMPLETE, RunStatus.FAILED, RunStatus.CANCELLED})


class RunStateError(ValueError):
    """The run is not in a state that allows the requested action."""


@dataclass(frozen=True)
class RunEnvironment:
    db: Database
    runner: StepRunner
    prompts: PromptLibrary
    workspaces: Path
    adapters: AdapterFactory
    max_parallel_runs: int = 2
    applications: ApplyDesk | None = None


class RunManager:
    def __init__(self, env: RunEnvironment, flows: tuple[AnyFlow, ...]) -> None:
        self._env = env
        self._flows = {f.pipeline: f for f in flows}
        self.log = RunLog(env.db)
        self._slots = asyncio.Semaphore(env.max_parallel_runs)
        self._tasks: dict[int, asyncio.Task[None]] = {}

    def pipelines(self) -> list[str]:
        return sorted(self._flows)

    def phases(self) -> dict[str, list[str]]:
        """Each pipeline's phases in run order, for showing a run's progress."""
        return {name: [p.name for p in flow.phases] for name, flow in sorted(self._flows.items())}

    def start(self, project_id: int, pipeline: str, state: JsonObject) -> int:
        flow = self._flow(pipeline)
        self._check_ready(flow, project_id)
        initial = flow.state_type.model_validate(state).model_dump(mode="json")
        with self._env.db.session() as session:
            run = Run(project_id=project_id, pipeline=pipeline, state=initial)
            session.add(run)
            session.flush()
            run_id = run.id
        logger.info("run %d: %s for project %d", run_id, pipeline, project_id)
        self._launch(run_id)
        return run_id

    def answer_gate(self, run_id: int, answer: GateAnswer) -> None:
        with self._env.db.session() as session:
            run = self._get(session, run_id)
            if run.status != RunStatus.AWAITING_GATE or run.pending_gate is None:
                raise RunStateError(f"run {run_id} is {run.status}, not waiting at a gate")
            kind = str(run.pending_gate["kind"])
            flow = self._flow(run.pipeline)
            phase = flow.phases[flow.index(run.phase)]
            if answer.revised is not None and isinstance(phase.run, Approval):
                phase.run.check(answer.revised)
            run.state = {**run.state, "answers": {kind: answer.model_dump(mode="json")}}
            run.pending_gate = None
            run.status = RunStatus.PENDING
        self.log.append(run_id, "gate_answered", {"kind": kind, "approve": answer.approve})
        self._launch(run_id)

    def resume(self, run_id: int) -> None:
        """Continue a failed, cancelled or interrupted run from the phase it stopped in."""
        with self._env.db.session() as session:
            run = self._get(session, run_id)
            if run.status not in (RunStatus.FAILED, RunStatus.CANCELLED):
                raise RunStateError(f"run {run_id} is {run.status}; only stopped runs resume")
            self._check_ready(self._flow(run.pipeline), run.project_id)
            run.status, run.error, run.finished_at = RunStatus.PENDING, None, None
        self._launch(run_id)

    def cancel(self, run_id: int) -> None:
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            return
        with self._env.db.session() as session:
            run = self._get(session, run_id)
            if run.status in FINISHED:
                raise RunStateError(f"run {run_id} already ended as {run.status}")
            self._finish(run, RunStatus.CANCELLED, None)
        self.log.append(run_id, "status", {"status": RunStatus.CANCELLED})

    def recover(self) -> None:
        """Runs a previous process left running can't be resumed silently; mark them failed."""
        with self._env.db.session() as session:
            stale = session.scalars(
                select(Run).where(Run.status.in_([RunStatus.RUNNING, RunStatus.PENDING]))
            )
            for run in stale:
                self._finish(run, RunStatus.FAILED, "interrupted: the server stopped mid-run")
                logger.warning("run %d was interrupted by a restart", run.id)

    async def wait(self, run_id: int) -> None:
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def shutdown(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    def _launch(self, run_id: int) -> None:
        self._tasks[run_id] = asyncio.get_running_loop().create_task(self._execute(run_id))

    async def _execute(self, run_id: int) -> None:
        async with self._slots:
            with self._env.db.session() as session:
                run = self._get(session, run_id)
                flow = self._flow(run.pipeline)
                ctx = self._context(run, flow)
                index = flow.index(run.phase)
                run.status = RunStatus.RUNNING
            self.log.append(run_id, "status", {"status": RunStatus.RUNNING})
            try:
                await self._phases(ctx, flow, index)
            except asyncio.CancelledError:
                self._end(ctx, RunStatus.CANCELLED, None)
                raise
            except Exception as exc:
                logger.exception("run %d failed", run_id)
                self._end(ctx, RunStatus.FAILED, f"{type(exc).__name__}: {exc}")

    async def _phases(self, ctx: FlowContext[RunState], flow: AnyFlow, index: int) -> None:
        while index < len(flow.phases):
            phase = flow.phases[index]
            self._save(ctx, phase=phase.name)
            ctx.emit("phase", {"phase": phase.name})
            result = await phase.run(ctx)
            if isinstance(result, GateRequest):
                self._save(ctx, gate=result)
                ctx.emit("gate", result.model_dump(mode="json"))
                return
            index = index + 1 if result is None else flow.index(result)
        self._end(ctx, RunStatus.COMPLETE, None)

    def _context(self, run: Run, flow: AnyFlow) -> FlowContext[RunState]:
        workspace = self._env.workspaces / f"project-{run.project_id}"
        log, run_id = self.log, run.id

        def on_event(event: AgentEvent) -> None:
            log.append(
                run_id, "agent", {"agent": event.agent, "kind": event.kind, "detail": event.detail}
            )

        return FlowContext(
            run_id=run.id,
            project_id=run.project_id,
            state=flow.state_type.model_validate(run.state),
            steps=PipelineSteps(self._env.runner, self._env.prompts, workspace, on_event),
            db=self._env.db,
            log=self.log,
            today=date.today(),
            workspace=workspace,
            adapters=self._env.adapters,
            applications=self._env.applications,
            answer_gate=lambda answer: self.answer_gate(run_id, answer),
        )

    def _save(
        self,
        ctx: FlowContext[RunState],
        *,
        phase: str | None = None,
        gate: GateRequest | None = None,
    ) -> None:
        with self._env.db.session() as session:
            run = self._get(session, ctx.run_id)
            run.state = ctx.state.model_dump(mode="json")
            run.cost_usd = run.cost_usd + ctx.steps.cost_usd
            ctx.steps.cost_usd = 0.0
            if phase is not None:
                run.phase = phase
            if gate is not None:
                run.status = RunStatus.AWAITING_GATE
                run.pending_gate = gate.model_dump(mode="json")

    def _end(self, ctx: FlowContext[RunState], status: RunStatus, error: str | None) -> None:
        self._save(ctx)
        with self._env.db.session() as session:
            self._finish(self._get(session, ctx.run_id), status, error)
        ctx.emit("status", {"status": status, "error": error})
        logger.info("run %d ended: %s", ctx.run_id, status)

    @staticmethod
    def _finish(run: Run, status: RunStatus, error: str | None) -> None:
        run.status, run.error, run.finished_at = status, error, utcnow()
        # A finished run resumes from its last phase, so a gate left open is dropped.
        run.pending_gate = None

    def blocker(self, project_id: int, pipeline: str) -> str | None:
        """Why `pipeline` can't run for the project yet, or None when it can."""
        flow = self._flow(pipeline)
        return flow.blocker(self._env.db, project_id) if flow.blocker else None

    def _check_ready(self, flow: AnyFlow, project_id: int) -> None:
        reason = self.blocker(project_id, flow.pipeline)
        if reason is not None:
            logger.info("%s refused for project %d: %s", flow.pipeline, project_id, reason)
            raise RunStateError(reason)

    def _flow(self, pipeline: str) -> AnyFlow:
        flow = self._flows.get(pipeline)
        if flow is None:
            raise LookupError(f"unknown pipeline {pipeline!r}; known: {self.pipelines()}")
        return flow

    @staticmethod
    def _get(session: Session, run_id: int) -> Run:
        run = session.get(Run, run_id)
        if run is None:
            raise LookupError(f"no run {run_id}")
        return run
