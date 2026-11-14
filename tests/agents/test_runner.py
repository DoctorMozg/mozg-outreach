import asyncio
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, TextBlock, ToolUseBlock

from mozg_outreach.agents import (
    AgentEvent,
    AgentOutputError,
    AgentPrompt,
    AgentRunError,
    AgentRunner,
    RunnerSettings,
)
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
        assert [(e.kind, e.detail) for e in events] == [
            ("started", "sonnet"),
            ("tool_use", "WebSearch"),
            ("text", "Found one."),
            ("finished", "$0.2500"),
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
