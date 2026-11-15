"""Run summaries shared by every mode: patterns across the scored items, cited by item id."""

import logging
from typing import Literal

from pydantic import BaseModel, Field

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.domain import FieldScalar, FieldValue, ItemScore, ScoreAxis
from mozg_outreach.pipelines.biz.models import CONCERNS_DESCRIPTION
from mozg_outreach.pipelines.freelance.models import FreelanceStrategy
from mozg_outreach.pipelines.seeker.models import JobStrategy
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.storage.db import Database
from mozg_outreach.storage.models import Item

logger = logging.getLogger(__name__)

type Strategy = JobStrategy | FreelanceStrategy


class ScoredItem(BaseModel):
    id: int
    title: str
    score: float | None
    axes: list[ScoreAxis]
    blockers: list[str]
    fields: dict[str, FieldScalar]


class Pattern(BaseModel):
    pattern: str
    item_ids: list[int] = Field(min_length=1, description="Ids from `items` the pattern rests on.")


class RunSummary(BaseModel):
    summary: str
    patterns: list[Pattern]
    recommendations: list[str]
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


class SeekerSummaryInput(BaseModel):
    mode: Literal["job", "freelance"]
    preferences: str = Field(description="The seeker's own words about the work they want.")
    target_titles: list[str]
    must_have_skills: list[str]
    must_have_or_groups: list[list[str]]
    items: list[ScoredItem] = Field(min_length=1, description="Best first.")

    @classmethod
    def build(
        cls,
        mode: Literal["job", "freelance"],
        preferences: str,
        strategy: Strategy,
        items: list[ScoredItem],
    ) -> "SeekerSummaryInput":
        return cls(
            mode=mode,
            preferences=preferences,
            target_titles=strategy.cv.top_titles,
            must_have_skills=strategy.must_have_skills,
            must_have_or_groups=strategy.must_have_or_groups,
            items=best_first(items),
        )


class SummaryOutcome(BaseModel):
    summary: RunSummary | None
    concerns: list[str]


def scored_item(item: Item) -> ScoredItem:
    """What a summarizer sees of one stored item: its score and field values."""
    score = ItemScore.model_validate(item.score) if item.score else None
    return ScoredItem(
        id=item.id,
        title=item.title,
        score=score.total if score else None,
        axes=score.axes if score else [],
        blockers=score.blockers if score else [],
        fields={k: FieldValue.model_validate(v).value for k, v in item.fields.items()},
    )


def stored_items(db: Database, item_ids: list[int]) -> list[ScoredItem]:
    with db.session() as session:
        found = [session.get(Item, i) for i in item_ids]
        return [scored_item(item) for item in found if item is not None]


def best_first[TItem: ScoredItem](items: list[TItem]) -> list[TItem]:
    return sorted(items, key=lambda i: -1 if i.score is None else i.score, reverse=True)


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


class SeekerRunSummarizer:
    def __init__(self, steps: PipelineSteps) -> None:
        self._steps = steps

    async def summarize(self, payload: SeekerSummaryInput) -> SummaryOutcome:
        """The report is complete without a summary, so a failed one is a concern, not a failure."""
        try:
            result = await self._steps.run("shared", "seeker-run-summarizer", payload, RunSummary)
        except (AgentRunError, AgentOutputError) as exc:
            logger.warning("run summary failed: %s", exc)
            return SummaryOutcome(summary=None, concerns=[f"No run summary: {exc}"])
        summary = cited_only(result, {item.id for item in payload.items})
        return SummaryOutcome(summary=summary, concerns=summary.concerns)
