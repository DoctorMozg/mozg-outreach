"""Judge jobs and gigs in batches with one agent; Python scores what it reads."""

import asyncio
import logging

from pydantic import BaseModel

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.pipelines.seeker.models import (
    CvProfile,
    Discipline,
    JudgeInput,
    JudgeResult,
    Listing,
    ListingJudgment,
)
from mozg_outreach.pipelines.steps import PipelineSteps

logger = logging.getLogger(__name__)

JUDGE_BATCH = 10


class JudgeContext(BaseModel):
    cv: CvProfile
    discipline: Discipline | None
    deal_breakers: list[str]
    terms: list[str]


class Judgments(BaseModel):
    by_key: dict[str, ListingJudgment]
    concerns: list[str]


class ListingJudge:
    def __init__(self, steps: PipelineSteps) -> None:
        self._steps = steps

    async def judge(self, listings: list[Listing], context: JudgeContext) -> Judgments:
        """A failed batch becomes a concern, so its listings are scored without a judgment."""
        batches = [listings[i : i + JUDGE_BATCH] for i in range(0, len(listings), JUDGE_BATCH)]
        results = await asyncio.gather(
            *(
                self._steps.run("seeker", "seeker-judge", self._input(b, context), JudgeResult)
                for b in batches
            ),
            return_exceptions=True,
        )
        judgments = Judgments(by_key={}, concerns=[])
        for batch, result in zip(batches, results, strict=True):
            if isinstance(result, AgentRunError | AgentOutputError):
                logger.warning("judge failed for a batch of %d: %s", len(batch), result)
                judgments.concerns.append(f"Judge failed for {len(batch)} listings: {result}")
                continue
            if isinstance(result, BaseException):
                raise result
            wanted = {listing.key for listing in batch}
            judgments.by_key |= {j.key: j for j in result.judgments if j.key in wanted}
            judgments.concerns += result.concerns
        missing = [listing.title for listing in listings if listing.key not in judgments.by_key]
        if missing:
            judgments.concerns.append(f"No judgment for: {', '.join(missing)}")
        return judgments

    @staticmethod
    def _input(batch: list[Listing], context: JudgeContext) -> JudgeInput:
        return JudgeInput(
            cv=context.cv,
            discipline=context.discipline,
            deal_breakers=context.deal_breakers,
            listings=[listing.for_agents(context.terms) for listing in batch],
        )
