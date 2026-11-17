import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    CLIConnectionError,
    CLIJSONDecodeError,
    ProcessError,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
)

from mozg_outreach.agents import (
    AgentEvent,
    AgentOutputError,
    AgentPrompt,
    AgentRunError,
    AgentRunner,
    RunnerSettings,
)
from mozg_outreach.agents.toolkits import ToolkitSession, ToolkitUnavailable
from tests.agents.conftest import FakeQuery, ScoutInput, ScoutResult, result_message

VALID_OUTPUT = {"companies": [{"name": "Acme", "domain": "acme.io"}]}


def assistant(*blocks: TextBlock | ToolUseBlock) -> AssistantMessage:
    return AssistantMessage(content=list(blocks), model="claude-sonnet-5-5")


class TestSuccess:
    async def test_returns_validated_value_and_events(
        self, sample_prompt: AgentPrompt, tmp_path: Path
    ) -> None:
        fake = FakeQuery(
            [
                assistant(ToolUseBlock(id="t1", name="WebSearch", input={"query": "x"})),
                assistant(ToolUseBlock(id="t2", name="WebFetch", input={"url": "https://a.io"})),
                assistant(TextBlock(text="Found one.")),
                result_message(VALID_OUTPUT),
            ]
        )
        events: list[AgentEvent] = []
        runner = AgentRunner(RunnerSettings(), query_fn=fake)

        result = await runner.run(
            sample_prompt,
            ScoutInput(region="Berlin"),
            ScoutResult,
            workspace=tmp_path / "ws",
            on_event=events.append,
        )

        assert result.value == ScoutResult.model_validate(VALID_OUTPUT)
        assert result.cost_usd == 0.25
        assert result.models == ("claude-sonnet-5-5",)
        assert [(e.kind, e.detail, e.url) for e in events] == [
            ("started", "sonnet", None),
            ("tool_use", "WebSearch", None),
            ("tool_use", "WebFetch", "https://a.io"),
            ("text", "Found one.", None),
            ("finished", "$0.2500", None),
        ]

    async def test_session_is_isolated_and_schema_enforced(
        self, sample_prompt: AgentPrompt, tmp_path: Path
    ) -> None:
        fake = FakeQuery([result_message(VALID_OUTPUT)])
        runner = AgentRunner(RunnerSettings(default_budget_usd=0.7), query_fn=fake)

        await runner.run(
            sample_prompt, ScoutInput(region="Berlin"), ScoutResult, workspace=tmp_path
        )

        user_prompt, options = fake.calls[0]
        assert '"region": "Berlin"' in user_prompt
        assert options.system_prompt == "You are a scout."
        assert options.tools == ["WebFetch", "WebSearch"]
        assert options.allowed_tools == ["WebFetch", "WebSearch"]
        assert options.permission_mode == "dontAsk"
        assert options.setting_sources == []
        assert options.cwd == tmp_path
        assert options.max_budget_usd == 0.7
        assert options.max_turns == 12
        assert options.output_format == {
            "type": "json_schema",
            "schema": ScoutResult.model_json_schema(),
        }


class TestFailures:
    async def test_schema_mismatch(self, sample_prompt: AgentPrompt, tmp_path: Path) -> None:
        fake = FakeQuery([result_message({"companies": [{"name": "Acme"}]})])
        runner = AgentRunner(RunnerSettings(), query_fn=fake)
        with pytest.raises(AgentOutputError, match="domain"):
            await runner.run(sample_prompt, ScoutInput(region="x"), ScoutResult, workspace=tmp_path)

    async def test_missing_structured_output(
        self, sample_prompt: AgentPrompt, tmp_path: Path
    ) -> None:
        runner = AgentRunner(RunnerSettings(), query_fn=FakeQuery([result_message(None)]))
        with pytest.raises(AgentOutputError, match="no structured_output"):
            await runner.run(sample_prompt, ScoutInput(region="x"), ScoutResult, workspace=tmp_path)

    async def test_cli_error_keeps_subtype(
        self, sample_prompt: AgentPrompt, tmp_path: Path
    ) -> None:
        fake = FakeQuery([result_message(subtype="error_max_budget_usd", is_error=True)])
        runner = AgentRunner(RunnerSettings(), query_fn=fake)
        with pytest.raises(AgentRunError) as info:
            await runner.run(sample_prompt, ScoutInput(region="x"), ScoutResult, workspace=tmp_path)
        assert info.value.subtype == "error_max_budget_usd"

    @pytest.mark.parametrize(
        ("error", "subtype"),
        [
            (ProcessError("Command failed", exit_code=1, stderr="boom"), "ProcessError"),
            (CLIConnectionError("Failed to start Claude Code"), "CLIConnectionError"),
            (CLIJSONDecodeError("{oops", ValueError("bad json")), "CLIJSONDecodeError"),
        ],
    )
    async def test_sdk_failures_become_run_errors(
        self, sample_prompt: AgentPrompt, tmp_path: Path, error: Exception, subtype: str
    ) -> None:
        # Callers handle AgentRunError; a raw SDK error would surface as a 500.
        fake = FakeQuery([assistant(TextBlock("working"))], fail=error)
        runner = AgentRunner(RunnerSettings(), query_fn=fake)
        with pytest.raises(AgentRunError) as info:
            await runner.run(sample_prompt, ScoutInput(region="x"), ScoutResult, workspace=tmp_path)
        assert info.value.subtype == subtype
        assert info.value.__cause__ is error

    async def test_stream_without_result(self, sample_prompt: AgentPrompt, tmp_path: Path) -> None:
        runner = AgentRunner(RunnerSettings(), query_fn=FakeQuery([]))
        with pytest.raises(AgentRunError, match="no_result"):
            await runner.run(sample_prompt, ScoutInput(region="x"), ScoutResult, workspace=tmp_path)


async def test_concurrency_limit_is_shared(sample_prompt: AgentPrompt, tmp_path: Path) -> None:
    fake = FakeQuery([result_message(VALID_OUTPUT)], delay=0.02)
    runner = AgentRunner(RunnerSettings(max_concurrency=2), query_fn=fake)

    await asyncio.gather(
        *(
            runner.run(sample_prompt, ScoutInput(region=str(i)), ScoutResult, workspace=tmp_path)
            for i in range(5)
        )
    )

    assert len(fake.calls) == 5
    assert fake.max_active == 2


@pytest.mark.parametrize(("concurrency", "budget"), [(0, 1.0), (1, 0.0)])
def test_settings_reject_bad_limits(concurrency: int, budget: float) -> None:
    with pytest.raises(ValueError, match="must be"):
        RunnerSettings(max_concurrency=concurrency, default_budget_usd=budget)


class FakeToolkit:
    """Records how often a session opened and closed; can refuse to start."""

    name = "pages"

    def __init__(self, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.open = 0
        self.closed = 0

    @asynccontextmanager
    async def session(self) -> AsyncIterator[ToolkitSession]:
        if self.unavailable:
            raise ToolkitUnavailable("chrome is missing")
        self.open += 1
        try:
            yield ToolkitSession(
                server=create_sdk_mcp_server("pages"),
                tool_names=("mcp__pages__open",),
                env={"ENABLE_TOOL_SEARCH": "false"},
            )
        finally:
            self.closed += 1


@pytest.fixture
def toolkit_prompt(sample_prompt: AgentPrompt) -> AgentPrompt:
    return replace(sample_prompt, toolkits=("pages",))


class TestToolkits:
    async def test_a_session_is_mounted_for_the_run_and_closed_after(
        self, toolkit_prompt: AgentPrompt, tmp_path: Path
    ) -> None:
        fake = FakeQuery(
            [
                assistant(
                    ToolUseBlock(id="t1", name="mcp__browser__open", input={"url": "https://b.io"})
                ),
                result_message(VALID_OUTPUT),
            ]
        )
        toolkit = FakeToolkit()
        events: list[AgentEvent] = []
        runner = AgentRunner(RunnerSettings(), query_fn=fake, toolkits={"pages": toolkit})

        await runner.run(
            toolkit_prompt,
            ScoutInput(region="Berlin"),
            ScoutResult,
            workspace=tmp_path,
            on_event=events.append,
        )

        _, options = fake.calls[0]
        assert options.allowed_tools == ["WebFetch", "WebSearch", "mcp__pages__open"]
        assert isinstance(options.mcp_servers, dict)
        assert list(options.mcp_servers) == ["pages"]
        assert options.strict_mcp_config is True
        assert options.env["ENABLE_TOOL_SEARCH"] == "false"
        assert (toolkit.open, toolkit.closed) == (1, 1)
        assert [e.url for e in events if e.kind == "tool_use"] == ["https://b.io"]

    async def test_an_unavailable_toolkit_leaves_the_web_tools(
        self, toolkit_prompt: AgentPrompt, tmp_path: Path
    ) -> None:
        fake = FakeQuery([result_message(VALID_OUTPUT)])
        events: list[AgentEvent] = []
        runner = AgentRunner(
            RunnerSettings(), query_fn=fake, toolkits={"pages": FakeToolkit(unavailable=True)}
        )

        await runner.run(
            toolkit_prompt,
            ScoutInput(region="Berlin"),
            ScoutResult,
            workspace=tmp_path,
            on_event=events.append,
        )

        _, options = fake.calls[0]
        assert options.allowed_tools == ["WebFetch", "WebSearch"]
        assert options.mcp_servers == {}
        assert "pages unavailable: chrome is missing" in [e.detail for e in events]

    async def test_an_unknown_toolkit_is_a_configuration_error(
        self, toolkit_prompt: AgentPrompt, tmp_path: Path
    ) -> None:
        runner = AgentRunner(RunnerSettings(), query_fn=FakeQuery([]))
        with pytest.raises(ValueError, match="unknown toolkits"):
            await runner.run(
                toolkit_prompt, ScoutInput(region="x"), ScoutResult, workspace=tmp_path
            )
