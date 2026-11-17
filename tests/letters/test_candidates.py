from pathlib import Path

import pytest

from mozg_outreach.agents import AgentRunError, PromptLibrary
from mozg_outreach.letters.composer import Composition, Delivery, LetterComposer, WrittenLetter
from mozg_outreach.letters.frame import LetterFrame
from mozg_outreach.letters.models import (
    AxisScores,
    CandidateScore,
    JudgeInput,
    JudgeResult,
    LetterBrief,
    LetterDraft,
)
from mozg_outreach.naturalize.naturalizer import Naturalizer, VoiceContext
from mozg_outreach.pipelines.steps import PipelineSteps
from tests.pipelines.conftest import REPO_PROMPTS, RecordedRunner, Scripted

OBSERVATION = (
    "Congrats on the Series B for Katana. Growing teams often see the AWS bill climb faster "
    "than the team.\n\nWorth a look at how your setup compares?"
)
PROBLEM = (
    "Katana's AWS bill likely grows faster than the team after a Series B. That gap is where "
    "idle capacity hides.\n\nWorth a look at where yours sits?"
)
PEER = (
    "Another SaaS team cut its AWS bill after its Series B by shutting idle capacity. Katana "
    "may see the same pattern now.\n\nWorth a short note on how they did it?"
)
# Opens on the sender, so it breaks a hard rule even after its retry.
SELFISH = (
    "I hope you are well. Katana's AWS bill likely grows faster than the team after a "
    "Series B.\n\nWorth a look at where yours sits?"
)


def draft(body: str) -> LetterDraft:
    return LetterDraft(
        subject="Katana's AWS bill", body=body, hook_used="Series B", angle_used="cost", concerns=[]
    )


def judged(*totals: int) -> JudgeResult:
    """Scores labels A, B, C... with these totals, spread over the six axes."""

    def axes(total: int) -> AxisScores:
        base, extra = divmod(total, 6)
        values = [base + 1] * extra + [base] * (6 - extra)
        return AxisScores.model_validate(dict(zip(AxisScores.model_fields, values, strict=True)))

    return JudgeResult(
        candidates=[
            CandidateScore(label=label, scores=axes(total), reason="")
            for label, total in zip("ABC", totals, strict=False)
        ],
        concerns=[],
    )


class Harness:
    def __init__(self, brief: LetterBrief, frame: LetterFrame, tmp_path: Path) -> None:
        self.brief, self.frame, self.tmp_path = brief, frame, tmp_path
        self.runner = RecordedRunner({})

    async def compose(self, script: dict[str, list[Scripted]]) -> WrittenLetter:
        # The naturalizer is down, so the chosen draft is kept as it is; choosing is under test.
        off = AgentRunError("expert-naturalizer", "error_during_execution", "off")
        self.runner = RecordedRunner({"expert-naturalizer": [off, off], **script})
        steps = PipelineSteps(self.runner, PromptLibrary(REPO_PROMPTS), self.tmp_path)
        job = Composition(
            brief=self.brief,
            frame=self.frame,
            voice=VoiceContext(profile=None, samples=[]),
            delivery=Delivery(address="jane@katana.example", confidence="published"),
            candidates=3,
        )
        letter = await LetterComposer(steps, Naturalizer(steps)).compose(job)
        assert isinstance(letter, WrittenLetter), letter
        return letter


@pytest.fixture
def harness(email_brief: LetterBrief, email_frame: LetterFrame, tmp_path: Path) -> Harness:
    return Harness(email_brief, email_frame, tmp_path)


class TestCandidates:
    async def test_each_draft_opens_its_own_way(self, harness: Harness) -> None:
        await harness.compose(
            {
                "expert-copywriter": [draft(OBSERVATION), draft(PROBLEM), draft(PEER)],
                "letter-judge": [judged(20, 20, 20), judged(20, 20, 20)],
            }
        )

        briefs = harness.runner.payloads["expert-copywriter"]
        variants = [b.variant for b in briefs if isinstance(b, LetterBrief)]
        assert len(set(variants)) == 3
        assert all(variants)

    async def test_the_second_order_cancels_the_judges_position_bias(
        self, harness: Harness
    ) -> None:
        # The first call likes position A; the rotated call shows PROBLEM is better.
        letter = await harness.compose(
            {
                "expert-copywriter": [draft(OBSERVATION), draft(PROBLEM), draft(PEER)],
                "letter-judge": [judged(29, 25, 20), judged(29, 20, 18)],
            }
        )

        [first, rotated] = harness.runner.payloads["letter-judge"]
        assert isinstance(first, JudgeInput)
        assert isinstance(rotated, JudgeInput)
        assert [c.body for c in first.candidates] == [OBSERVATION, PROBLEM, PEER]
        assert [c.body for c in rotated.candidates] == [PROBLEM, PEER, OBSERVATION]
        review = letter.review
        assert PROBLEM.split("\n")[0] in review.before_naturalize
        assert [(c.variant, c.score, c.chosen) for c in review.candidates] == [
            ("observation", 47, False),
            ("problem", 54, True),
            ("peer", 40, False),
        ]

    async def test_a_draft_with_hard_problems_loses_to_clean_ones(self, harness: Harness) -> None:
        letter = await harness.compose(
            {
                "expert-copywriter": [draft(SELFISH), draft(SELFISH), draft(PROBLEM), draft(PEER)],
                # Both calls prefer PEER, which comes first in the rotated call.
                "letter-judge": [judged(20, 25), judged(25, 20)],
            }
        )

        [first, _] = harness.runner.payloads["letter-judge"]
        assert isinstance(first, JudgeInput)
        assert [c.body for c in first.candidates] == [PROBLEM, PEER], "the selfish one is out"
        chosen = [(c.variant, c.chosen, bool(c.problems)) for c in letter.review.candidates]
        assert chosen == [
            ("observation", False, True),
            ("problem", False, False),
            ("peer", True, False),
        ]

    async def test_a_lone_clean_draft_needs_no_judge(self, harness: Harness) -> None:
        bad = [draft(SELFISH)] * 4
        letter = await harness.compose({"expert-copywriter": [*bad, draft(PEER)]})

        assert "letter-judge" not in harness.runner.payloads
        assert [c.chosen for c in letter.review.candidates] == [False, False, True]

    async def test_a_failed_judge_keeps_the_first_clean_draft(self, harness: Harness) -> None:
        down = AgentRunError("letter-judge", "error_during_execution", "down")
        letter = await harness.compose(
            {
                "expert-copywriter": [draft(OBSERVATION), draft(PROBLEM), draft(PEER)],
                "letter-judge": [down, down],
            }
        )

        assert [c.chosen for c in letter.review.candidates] == [True, False, False]
        assert any(n.startswith("The judge failed") for n in letter.review.review_notes)

    async def test_a_failed_draft_leaves_the_others(self, harness: Harness) -> None:
        down = AgentRunError("expert-copywriter", "error_during_execution", "down")
        letter = await harness.compose(
            {
                "expert-copywriter": [down, draft(PROBLEM), draft(PEER)],
                "letter-judge": [judged(20, 25), judged(20, 25)],
            }
        )

        assert [c.variant for c in letter.review.candidates] == ["problem", "peer"]
        assert letter.review.review_notes[0].startswith("1 of 3 drafts failed")
