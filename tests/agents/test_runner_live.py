from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import BaseModel

from mozg_outreach.agents import AgentPrompt, AgentRunner, RunnerSettings
from tests.agents.conftest import ScoutInput, ScoutResult

pytestmark = pytest.mark.live


class Verdict(BaseModel):
    fit: bool
    reason: str


class Candidate(BaseModel):
    name: str


async def test_web_agent_returns_nested_schema(sample_prompt: AgentPrompt, tmp_path: Path) -> None:
    # ScoutResult nests Company, so its JSON schema uses $defs/$ref.
    runner = AgentRunner(RunnerSettings(default_budget_usd=0.8))
    prompt = replace(sample_prompt, body="Find exactly 2 real companies with WebSearch.")

    result = await runner.run(
        prompt, ScoutInput(region="Tallinn game studios"), ScoutResult, workspace=tmp_path
    )

    assert len(result.value.companies) >= 1
    assert all(c.domain for c in result.value.companies)
    assert result.cost_usd > 0


async def test_toolless_agent_still_returns_structured_output(
    sample_prompt: AgentPrompt, tmp_path: Path
) -> None:
    runner = AgentRunner(RunnerSettings(default_budget_usd=0.3))
    prompt = replace(sample_prompt, tools=(), body="Decide if the candidate is a software company.")

    result = await runner.run(prompt, Candidate(name="GitHub"), Verdict, workspace=tmp_path)

    assert result.value.fit is True
    assert result.value.reason
