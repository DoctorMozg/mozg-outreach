from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from mozg_outreach.agents import EventSink, PromptLibrary, StepRunner


class PipelineSteps:
    """Runs named agent steps for one pipeline run and totals their cost."""

    def __init__(
        self,
        runner: StepRunner,
        prompts: PromptLibrary,
        workspace: Path,
        on_event: EventSink | None = None,
    ) -> None:
        self._runner = runner
        self._prompts = prompts
        self._workspace = workspace
        self._on_event = on_event
        self.cost_usd = 0.0

    async def run[TResult: BaseModel](
        self,
        group: str,
        agent: str,
        payload: BaseModel,
        result_type: type[TResult],
        screenshots: Sequence[bytes] = (),
    ) -> TResult:
        result = await self._runner.run(
            self._prompts.get(group, agent),
            payload,
            result_type,
            workspace=self._workspace,
            on_event=self._on_event,
            screenshots=screenshots,
        )
        self.cost_usd += result.cost_usd
        return result.value
