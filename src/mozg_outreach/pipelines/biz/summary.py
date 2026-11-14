"""Summarize a finished biz search: patterns across the scored companies, cited by item id."""

import logging
from typing import Literal

from pydantic import BaseModel, Field

from mozg_outreach.domain import FieldScalar, FieldValue, ItemScore, ScoreAxis
from mozg_outreach.pipelines.biz.assessment_models import CompanyAssessment
from mozg_outreach.pipelines.biz.models import (
    CONCERNS_DESCRIPTION,
    GoalAnalysis,
    OutreachAngle,
    Strategy,
    TargetProfile,
)
from mozg_outreach.pipelines.biz.outreach import ASSESSMENT
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.storage.models import Item

logger = logging.getLogger(__name__)


class SummaryItem(BaseModel):
    id: int
    title: str
    score: float | None
    axes: list[ScoreAxis]
    blockers: list[str]
    sector_match: Literal["exact", "adjacent", "unrelated"] | None
    fields: dict[str, FieldScalar]


class SummaryInput(BaseModel):
    goal: GoalAnalysis
    target_profile: TargetProfile
    outreach_angles: list[OutreachAngle]
    items: list[SummaryItem] = Field(min_length=1)


class Pattern(BaseModel):
    pattern: str
    item_ids: list[int] = Field(min_length=1, description="Ids from `items` the pattern rests on.")


class RunSummary(BaseModel):
    summary: str
    patterns: list[Pattern]
    recommendations: list[str]
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


class SummaryRequest(BaseModel):
    strategy: Strategy
    items: list[SummaryItem] = Field(min_length=1)


def summary_item(item: Item) -> SummaryItem:
    """What the summarizer sees of one stored company: scores, verdict and field values."""
    score = ItemScore.model_validate(item.score) if item.score else None
    stored = item.details.get(ASSESSMENT)
    verdict = CompanyAssessment.model_validate(stored).verdict if stored else None
    return SummaryItem(
        id=item.id,
        title=item.title,
        score=score.total if score else None,
        axes=score.axes if score else [],
        blockers=score.blockers if score else [],
        sector_match=verdict.sector_match if verdict else None,
        fields={k: FieldValue.model_validate(v).value for k, v in item.fields.items()},
    )


class BizRunSummarizer:
    def __init__(self, steps: PipelineSteps) -> None:
        self._steps = steps

    async def summarize(self, request: SummaryRequest) -> RunSummary:
        payload = SummaryInput(
            goal=request.strategy.goal_analysis,
            target_profile=request.strategy.target_profile,
            outreach_angles=request.strategy.outreach_angles,
            items=request.items,
        )
        result = await self._steps.run("biz", "run-summarizer", payload, RunSummary)
        return cited_only(result, {item.id for item in request.items})


def cited_only(summary: RunSummary, known: set[int]) -> RunSummary:
    """Strip citations of items the run doesn't have; a pattern left without any is dropped."""
    patterns: list[Pattern] = []
    concerns = list(summary.concerns)
    for pattern in summary.patterns:
        unknown = [i for i in pattern.item_ids if i not in known]
        kept = [i for i in pattern.item_ids if i in known]
        if unknown:
            logger.warning("summary cited unknown items %s", unknown)
            concerns.append(f"The summary cited items this run doesn't have: {unknown}.")
        if kept:
            patterns.append(Pattern(pattern=pattern.pattern, item_ids=kept))
    return summary.model_copy(update={"patterns": patterns, "concerns": concerns})
