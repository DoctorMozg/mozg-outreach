import asyncio
import logging
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    Message,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)
from claude_agent_sdk import query as sdk_query
from pydantic import BaseModel, ValidationError

from mozg_outreach.agents.prompt import AgentPrompt

logger = logging.getLogger(__name__)


class QueryFn(Protocol):
    def __call__(self, *, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]: ...


@dataclass(frozen=True)
class RunnerSettings:
    max_concurrency: int = 3
    default_budget_usd: float = 1.0

    def __post_init__(self) -> None:
        if self.max_concurrency < 1:
            raise ValueError(f"max_concurrency must be >= 1, got {self.max_concurrency}")
        if self.default_budget_usd <= 0:
            raise ValueError(f"default_budget_usd must be > 0, got {self.default_budget_usd}")


@dataclass(frozen=True)
class AgentEvent:
    agent: str
    kind: Literal["started", "tool_use", "text", "finished"]
    detail: str
    url: str | None = None


EventSink = Callable[[AgentEvent], None]


@dataclass(frozen=True)
class AgentResult[TResult: BaseModel]:
    value: TResult
    cost_usd: float
    duration_ms: int
    num_turns: int
    session_id: str
    models: tuple[str, ...]


class StepRunner(Protocol):
    """What pipelines need from AgentRunner; tests substitute a recorded-response fake."""

    async def run[TResult: BaseModel](
        self,
        prompt: AgentPrompt,
        payload: BaseModel,
        result_type: type[TResult],
        *,
        workspace: Path,
        budget_usd: float | None = None,
        on_event: EventSink | None = None,
    ) -> AgentResult[TResult]: ...


class AgentRunError(RuntimeError):
    """The CLI ended the run with an error (budget, turns, interrupt, crash)."""

    def __init__(self, agent: str, subtype: str, message: str) -> None:
        super().__init__(f"{agent}: run failed ({subtype}): {message}")
        self.agent = agent
        self.subtype = subtype


class AgentOutputError(ValueError):
    """The run finished but its structured output is missing or doesn't match the schema."""

    def __init__(self, agent: str, message: str) -> None:
        super().__init__(f"{agent}: bad structured output: {message}")
        self.agent = agent


class AgentRunner:
    """Runs one agent step as one Claude Code session with schema-enforced output."""

    def __init__(self, settings: RunnerSettings, query_fn: QueryFn = sdk_query) -> None:
        self._settings = settings
        self._query = query_fn
        self._slots = asyncio.Semaphore(settings.max_concurrency)
        if os.environ.get("ANTHROPIC_API_KEY"):
            logger.warning(
                "ANTHROPIC_API_KEY is set: agent runs bill the API, not the subscription"
            )

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
        workspace.mkdir(parents=True, exist_ok=True)
        options = self._options(prompt, result_type, workspace, budget_usd)
        emit = on_event or (lambda _event: None)

        async with self._slots:
            logger.info("agent %s/%s started in %s", prompt.group, prompt.name, workspace)
            emit(AgentEvent(prompt.name, "started", prompt.model))
            result = await self._consume(prompt.name, render_input(payload), options, emit)

        if result.is_error or result.subtype != "success":
            raise AgentRunError(prompt.name, result.subtype, result.result or "no detail")
        if result.structured_output is None:
            raise AgentOutputError(prompt.name, "run returned no structured_output")
        try:
            value = result_type.model_validate(result.structured_output)
        except ValidationError as exc:
            raise AgentOutputError(prompt.name, str(exc)) from exc

        cost = result.total_cost_usd or 0.0
        logger.info("agent %s finished: $%.4f, %d turns", prompt.name, cost, result.num_turns)
        emit(AgentEvent(prompt.name, "finished", f"${cost:.4f}"))
        return AgentResult(
            value=value,
            cost_usd=cost,
            duration_ms=result.duration_ms,
            num_turns=result.num_turns,
            session_id=result.session_id,
            models=tuple((result.model_usage or {}).keys()),
        )

    def _options(
        self,
        prompt: AgentPrompt,
        result_type: type[BaseModel],
        workspace: Path,
        budget_usd: float | None,
    ) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            system_prompt=prompt.body,
            tools=list(prompt.tools),
            allowed_tools=list(prompt.tools),
            permission_mode="dontAsk",
            output_format={"type": "json_schema", "schema": result_type.model_json_schema()},
            setting_sources=[],
            env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"},
            cwd=workspace,
            model=prompt.model,
            effort=prompt.effort,
            max_turns=prompt.max_turns,
            max_budget_usd=budget_usd or self._settings.default_budget_usd,
        )

    async def _consume(
        self, agent: str, user_prompt: str, options: ClaudeAgentOptions, emit: EventSink
    ) -> ResultMessage:
        result: ResultMessage | None = None
        async for message in self._query(prompt=user_prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        # Kept so a caller can check that a cited page was actually read.
                        url = block.input.get("url") if block.name == "WebFetch" else None
                        emit(
                            AgentEvent(agent, "tool_use", block.name, url=str(url) if url else None)
                        )
                    elif isinstance(block, TextBlock) and block.text.strip():
                        emit(AgentEvent(agent, "text", block.text))
            elif isinstance(message, ResultMessage):
                result = message
        if result is None:
            raise AgentRunError(agent, "no_result", "stream ended without a ResultMessage")
        return result


def render_input(payload: BaseModel) -> str:
    return f"Input for this step:\n\n```json\n{payload.model_dump_json(indent=2)}\n```"
