"""Summarize a finished biz search over its scored companies and the strategy behind them."""

from typing import Literal

from pydantic import BaseModel, Field

from mozg_outreach.pipelines.biz.assessment_models import CompanyAssessment
from mozg_outreach.pipelines.biz.models import (
    GoalAnalysis,
    OutreachAngle,
    Strategy,
    TargetProfile,
)
from mozg_outreach.pipelines.biz.outreach import ASSESSMENT
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.pipelines.summary import RunSummary, ScoredItem, cited_only, scored_item
from mozg_outreach.storage.models import Item


class SummaryItem(ScoredItem):
    sector_match: Literal["exact", "adjacent", "unrelated"] | None


class SummaryInput(BaseModel):
    goal: GoalAnalysis
    target_profile: TargetProfile
    outreach_angles: list[OutreachAngle]
    items: list[SummaryItem] = Field(min_length=1)


class SummaryRequest(BaseModel):
    strategy: Strategy
    items: list[SummaryItem] = Field(min_length=1)


def summary_item(item: Item) -> SummaryItem:
    """A scored company plus the fit verdict's sector match."""
    stored = item.details.get(ASSESSMENT)
    verdict = CompanyAssessment.model_validate(stored).verdict if stored else None
    return SummaryItem(
        **scored_item(item).model_dump(), sector_match=verdict.sector_match if verdict else None
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
