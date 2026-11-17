"""Picks the best of several drafts of one letter with a separate judge, scored in Python."""

import asyncio
import logging
import string
from collections.abc import Sequence

from pydantic import BaseModel, Field

from mozg_outreach.agents import AgentOutputError
from mozg_outreach.letters.models import (
    JudgedLetter,
    JudgeInput,
    JudgeResult,
    LetterBrief,
    LetterDraft,
)
from mozg_outreach.pipelines.steps import PipelineSteps

logger = logging.getLogger(__name__)


class Pick(BaseModel):
    winner: int = Field(description="Index into the drafts given.")
    scores: list[int] = Field(description="Points per draft, summed over both orders.")


class LetterJudge:
    def __init__(self, steps: PipelineSteps) -> None:
        self._steps = steps

    async def pick(self, brief: LetterBrief, drafts: Sequence[LetterDraft]) -> Pick:
        """Two judge calls, the second with the drafts rotated, so position bias cancels out."""
        order = list(range(len(drafts)))
        orders = [order, order[1:] + order[:1]]
        results = await asyncio.gather(*(self._judge(brief, drafts, o) for o in orders))
        scores = [0] * len(drafts)
        for points in results:
            for index, total in points.items():
                scores[index] += total
        # Ties go to the earlier draft, the observation opener, which needs the least proof.
        winner = max(range(len(drafts)), key=lambda i: (scores[i], -i))
        logger.info("judge scores for %s: %s", brief.recipient.name, scores)
        return Pick(winner=winner, scores=scores)

    async def _judge(
        self, brief: LetterBrief, drafts: Sequence[LetterDraft], order: list[int]
    ) -> dict[int, int]:
        labels = dict(zip(string.ascii_uppercase, order, strict=False))
        payload = JudgeInput(
            channel=brief.channel,
            language=brief.language,
            recipient=brief.recipient,
            purpose=brief.touch.purpose if brief.touch else "open",
            hooks=brief.hooks,
            ask=brief.ask,
            sender_voice=brief.sender_voice,
            candidates=[
                JudgedLetter(label=label, subject=drafts[i].subject, body=drafts[i].body)
                for label, i in labels.items()
            ],
        )
        result = await self._steps.run("creative", "letter-judge", payload, JudgeResult)
        scored = {c.label: c.scores.total() for c in result.candidates}
        if set(scored) != set(labels):
            raise AgentOutputError(
                "letter-judge", f"scored {sorted(scored)}, expected {sorted(labels)}"
            )
        return {labels[label]: total for label, total in scored.items()}
