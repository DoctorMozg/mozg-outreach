import json
from pathlib import Path

import pytest

from mozg_outreach.agents import PromptLibrary
from mozg_outreach.naturalize.voice import VoiceNotes, VoiceProfileInput, VoiceProfiler
from mozg_outreach.pipelines.steps import PipelineSteps
from tests.pipelines.conftest import REPO_PROMPTS, RecordedRunner

FIXTURES = Path(__file__).parent / "fixtures"
NOTES = VoiceNotes(
    tone="Direct and friendly.",
    habits=["Opens with the ask."],
    vocabulary=["quick one", "no rush"],
    avoid=["Long legal footer."],
    concerns=[],
)


@pytest.fixture
def casual() -> list[str]:
    loaded: list[str] = json.loads((FIXTURES / "casual.json").read_text())
    return loaded


def profiler(runner: RecordedRunner, tmp_path: Path) -> VoiceProfiler:
    return VoiceProfiler(PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path))


async def test_profile_combines_measured_notes_and_user_prompts(
    casual: list[str], tmp_path: Path
) -> None:
    runner = RecordedRunner({"voice-profiler": [NOTES]})
    prompt = "Never open with 'I hope this finds you well'.\nSign as Max, not Maxim."

    profile = await profiler(runner, tmp_path).build("email", casual, [prompt])

    payload = runner.payloads["voice-profiler"][0]
    assert isinstance(payload, VoiceProfileInput)
    assert payload.measured == profile.features
    rendered = profile.render()
    assert "Measured on 4 messages" in rendered
    assert "greetings used: hey (3), hi (1)" in rendered
    assert "- Opens with the ask." in rendered
    assert 'Own words and phrases: "quick one", "no rush"' in rendered
    # User instructions come last and verbatim, so they win over the measured habits.
    assert rendered.endswith(prompt)


async def test_too_few_samples_is_rejected_before_any_agent_call(
    casual: list[str], tmp_path: Path
) -> None:
    runner = RecordedRunner({})
    with pytest.raises(ValueError, match="at least 3 samples, got 2"):
        await profiler(runner, tmp_path).build("dm", casual[:2], [])
    assert runner.payloads == {}
