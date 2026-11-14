import asyncio
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest
from claude_agent_sdk import ClaudeAgentOptions, Message, ResultMessage
from claude_agent_sdk.types import ModelUsage
from pydantic import BaseModel

from mozg_outreach.agents import AgentPrompt, PromptLibrary

SAMPLE_PROMPT = """---
name: sample-scout
description: test agent
tools: Read, Write, Agent(other), WebFetch, WebSearch
model: sonnet
effort: medium
maxTurns: 12
---

You are a scout.
"""


class Company(BaseModel):
    name: str
    domain: str


class ScoutResult(BaseModel):
    companies: list[Company]


class ScoutInput(BaseModel):
    region: str


SONNET_USAGE = ModelUsage(
    inputTokens=100,
    outputTokens=50,
    cacheReadInputTokens=0,
    cacheCreationInputTokens=0,
    webSearchRequests=1,
    costUSD=0.25,
    contextWindow=200_000,
    maxOutputTokens=32_000,
)


def result_message(
    structured_output: object = None, *, subtype: str = "success", is_error: bool = False
) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=1200,
        duration_api_ms=1000,
        is_error=is_error,
        num_turns=3,
        session_id="sess-1",
        total_cost_usd=0.25,
        result="boom" if is_error else None,
        structured_output=structured_output,
        model_usage={"claude-sonnet-5-5": SONNET_USAGE},
    )


class FakeQuery:
    """Stands in for `claude_agent_sdk.query`: records options, replays scripted messages."""

    def __init__(self, messages: Sequence[Message], delay: float = 0.0) -> None:
        self.messages = list(messages)
        self.delay = delay
        self.calls: list[tuple[str, ClaudeAgentOptions]] = []
        self.active = 0
        self.max_active = 0

    async def __call__(self, *, prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[Message]:
        self.calls.append((prompt, options))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            for message in self.messages:
                yield message
        finally:
            self.active -= 1


@pytest.fixture
def prompt_root(tmp_path: Path) -> Path:
    root = tmp_path / "prompts"
    (root / "biz").mkdir(parents=True)
    (root / "biz" / "sample-scout.md").write_text(SAMPLE_PROMPT, encoding="utf-8")
    return root


@pytest.fixture
def sample_prompt(prompt_root: Path) -> AgentPrompt:
    return PromptLibrary(prompt_root).get("biz", "sample-scout")
