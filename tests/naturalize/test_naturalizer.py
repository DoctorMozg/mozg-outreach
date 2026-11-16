from pathlib import Path

import pytest

from mozg_outreach.agents import AgentRunError, PromptLibrary
from mozg_outreach.naturalize.models import NaturalizeInput, NaturalizeResult
from mozg_outreach.naturalize.naturalizer import (
    WORD_BUDGETS,
    Naturalizer,
    NaturalizeRequest,
    VoiceContext,
    habitual_punctuation,
    pick_exemplars,
)
from mozg_outreach.naturalize.stylometry import StyleAnalyzer
from mozg_outreach.naturalize.validation import RewriteOutcome
from mozg_outreach.naturalize.voice import VoiceNotes, VoiceProfile
from mozg_outreach.pipelines.steps import PipelineSteps
from tests.pipelines.conftest import REPO_PROMPTS, RecordedRunner, Scripted

DRAFT = (
    "Hi Anna,\n\n"
    "Congratulations on the Series B extension. Furthermore, scaling a platform team in Tallinn "
    "while keeping cloud spend in check is a delicate balance. We help B2B SaaS teams cut AWS "
    "spend by 35% in 8 weeks without slowing releases, and the engagement is fixed-price. "
    "Would a 20-minute call next week be useful? I can share the before and after numbers and "
    "the plan we used.\n\n"
    "Thanks,\nMax"
)
REWRITE = DRAFT.replace("Furthermore, scaling", "Scaling").replace(
    "is a delicate balance", "is hard"
)
SAMPLES = [
    "Hey Tom,\n\nquick one; can you send the deck?\n\nThanks,\nMax",
    "Hey Lena,\n\ngreat call; I'm in. Let's start Monday and I'll set up the repo and CI."
    "\n\nThanks,\nMax",
    "Hi Ben,\n\nrunning late on the migration, it's the DNS bit. Done tomorrow.\n\nCheers,\nMax",
]


@pytest.fixture
def voice() -> VoiceContext:
    notes = VoiceNotes(tone="Direct.", habits=["Ask first."], vocabulary=[], avoid=[], concerns=[])
    profile = VoiceProfile(
        channel="email", features=StyleAnalyzer().features(SAMPLES), notes=notes, style_prompts=[]
    )
    return VoiceContext(profile=profile, samples=SAMPLES)


async def run(
    script: list[Scripted], voice: VoiceContext, tmp_path: Path
) -> tuple[RewriteOutcome, RecordedRunner]:
    runner = RecordedRunner({"expert-naturalizer": script})
    naturalizer = Naturalizer(PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path))
    request = NaturalizeRequest(
        text=DRAFT,
        channel="email",
        language="en",
        word_budget=WORD_BUDGETS["email"],
        preserve=["Series B"],
    )
    return await naturalizer.naturalize(request, voice), runner


async def test_dispatch_is_medium_with_budget_voice_and_exemplars(
    voice: VoiceContext, tmp_path: Path
) -> None:
    rewrite = NaturalizeResult(
        text=REWRITE, changes=["Dropped a stock connector."], specificity_needed=[], concerns=[]
    )
    outcome, runner = await run([rewrite], voice, tmp_path)

    payload = runner.payloads["expert-naturalizer"][0]
    assert isinstance(payload, NaturalizeInput)
    assert payload.severity == "medium"
    assert payload.length_tolerance_pct == 10
    assert payload.word_budget is not None
    assert (payload.word_budget.min, payload.word_budget.max) == (60, 100)
    assert payload.allowed_punctuation == ["semicolon"]
    assert payload.voice_profile is not None
    assert "Habits to keep:" in payload.voice_profile
    assert payload.exemplars[0] == SAMPLES[1]
    assert outcome.text == REWRITE
    assert not outcome.reverted


async def test_agent_failure_keeps_the_draft(voice: VoiceContext, tmp_path: Path) -> None:
    failure = AgentRunError("expert-naturalizer", "error_max_turns", "out of turns")
    outcome, _ = await run([failure], voice, tmp_path)
    assert outcome.text == DRAFT
    assert outcome.reverted
    assert outcome.review_notes[0].startswith("Naturalizer failed")


class TestHelpers:
    def test_habitual_punctuation_needs_half_the_samples(self) -> None:
        assert habitual_punctuation(SAMPLES) == ["semicolon"]
        assert habitual_punctuation(SAMPLES[2:]) == []
        assert habitual_punctuation(["a; b", "c", "d"]) == []

    def test_exemplars_are_closest_in_length(self) -> None:
        samples = ["one two", "one two three four five six", "one two three four"]
        assert pick_exemplars(samples, "a b c d e", "en") == [samples[1], samples[2], samples[0]]

    def test_exemplars_in_the_letter_language_come_first(self) -> None:
        english = "Thanks for the call, I will send the notes tomorrow."
        russian = "Спасибо за звонок, завтра пришлю заметки и план."
        assert pick_exemplars([english, russian], "Добрый день, коллеги.", "ru") == [russian]
        assert pick_exemplars([english], "Добрый день, коллеги.", "ru") == [english]


class TestRetry:
    def result(self, text: str) -> NaturalizeResult:
        return NaturalizeResult(text=text, changes=[], specificity_needed=[], concerns=[])

    async def test_near_miss_is_retried_with_feedback(
        self, voice: VoiceContext, tmp_path: Path
    ) -> None:
        too_short = REWRITE.replace(
            " I can share the before and after numbers and the plan we used.", ""
        )
        outcome, runner = await run([self.result(too_short), self.result(REWRITE)], voice, tmp_path)

        assert outcome.text == REWRITE
        assert not outcome.reverted
        first, second = runner.payloads["expert-naturalizer"]
        assert isinstance(first, NaturalizeInput)
        assert isinstance(second, NaturalizeInput)
        assert first.retry is None
        assert second.retry is not None
        assert second.retry.previous_attempt == too_short
        assert any("budget 60-100" in p for p in second.retry.problems)

    async def test_two_misses_keep_the_draft(self, voice: VoiceContext, tmp_path: Path) -> None:
        dropped = REWRITE.replace("Series B extension", "funding round")
        outcome, runner = await run([self.result(dropped), self.result(dropped)], voice, tmp_path)

        assert outcome.text == DRAFT
        assert outcome.reverted
        assert "dropped:Series B" in [p.key for p in outcome.damage]
        assert len(runner.payloads["expert-naturalizer"]) == 2
