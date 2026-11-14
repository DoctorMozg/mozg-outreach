from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel

from mozg_outreach.agents import AgentPrompt, AgentResult, EventSink

REPO_PROMPTS = Path(__file__).resolve().parents[2] / "prompts"

type Scripted = BaseModel | Exception


class RecordedRunner:
    """StepRunner fake: replays scripted results per agent name, in call order."""

    def __init__(self, script: dict[str, list[Scripted]]) -> None:
        self._script = {name: list(results) for name, results in script.items()}
        self.payloads: defaultdict[str, list[BaseModel]] = defaultdict(list)

    async def run[TResult: BaseModel](
        self,
        prompt: AgentPrompt,
        payload: BaseModel,
        result_type: type[TResult],
        *,
        workspace: Path,
        budget_usd: float | None = None,
        on_event: EventSink | None = None,
    ) -> AgentResult[TResult]:
        self.payloads[prompt.name].append(payload)
        queue = self._script.get(prompt.name)
        if not queue:
            raise AssertionError(f"no scripted result left for {prompt.name}")
        scripted = queue.pop(0)
        if isinstance(scripted, Exception):
            raise scripted
        return AgentResult(
            value=result_type.model_validate(scripted.model_dump()),
            cost_usd=0.1,
            duration_ms=10,
            num_turns=1,
            session_id="recorded",
            models=("recorded",),
        )
