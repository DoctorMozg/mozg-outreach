from pathlib import Path

from sqlalchemy.orm import Session

from mozg_outreach.agents import PromptLibrary
from mozg_outreach.domain import FieldValue, ItemKind, ItemScore, ScoreAxis
from mozg_outreach.pipelines.biz.assessment_models import CompanyAssessment, FitVerdict
from mozg_outreach.pipelines.biz.models import Strategy
from mozg_outreach.pipelines.biz.outreach import ASSESSMENT
from mozg_outreach.pipelines.biz.summary import (
    BizRunSummarizer,
    SummaryInput,
    SummaryRequest,
    summary_item,
)
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.pipelines.summary import Pattern, RunSummary, cited_only
from mozg_outreach.storage.models import Item, Project
from tests.pipelines.biz.conftest import load_fixture
from tests.pipelines.conftest import REPO_PROMPTS, RecordedRunner


def summary(*patterns: Pattern) -> RunSummary:
    return RunSummary(
        summary="Two strong fits.", patterns=list(patterns), recommendations=[], concerns=[]
    )


class TestCitedOnly:
    def test_strips_unknown_ids_and_reports_them(self) -> None:
        result = cited_only(summary(Pattern(pattern="AWS", item_ids=[1, 99])), known={1, 2})

        assert result.patterns == [Pattern(pattern="AWS", item_ids=[1])]
        assert result.concerns == ["The summary cited items this run doesn't have: [99]."]

    def test_drops_a_pattern_resting_only_on_unknown_items(self) -> None:
        result = cited_only(
            summary(Pattern(pattern="ghost", item_ids=[7]), Pattern(pattern="real", item_ids=[2])),
            known={1, 2},
        )

        assert [p.pattern for p in result.patterns] == ["real"]
        assert len(result.concerns) == 1


def stored_item(session: Session, project: Project) -> Item:
    score = ItemScore(
        total=72.5,
        axes=[ScoreAxis(axis="fit", value=80, weight=1, reason="exact sector")],
        blockers=[],
    )
    verdict = load_fixture("fit_verdict", FitVerdict)
    item = Item(
        project_id=project.id,
        kind=ItemKind.COMPANY,
        dedup_key="katanamrp.com",
        title="Katana",
        fields={"cloud": FieldValue(value=["AWS"], source_url="https://k.io").model_dump()},
        score=score.model_dump(mode="json"),
        score_total=score.total,
        details={
            ASSESSMENT: CompanyAssessment(fields={}, verdict=verdict, concerns=[]).model_dump(
                mode="json"
            )
        },
    )
    session.add(item)
    session.flush()
    return item


def test_summary_item_reads_score_verdict_and_field_values(
    session: Session, project: Project
) -> None:
    item = stored_item(session, project)

    seen = summary_item(item)

    assert (seen.id, seen.title, seen.score) == (item.id, "Katana", 72.5)
    assert seen.sector_match == "exact"
    assert seen.fields == {"cloud": ["AWS"]}
    assert seen.axes[0].reason == "exact sector"


async def test_summarizer_sends_strategy_parts_and_filters_citations(
    tmp_path: Path, session: Session, project: Project, strategy: Strategy
) -> None:
    item = stored_item(session, project)
    runner = RecordedRunner(
        {"run-summarizer": [summary(Pattern(pattern="AWS", item_ids=[item.id, item.id + 50]))]}
    )
    steps = PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path)

    result = await BizRunSummarizer(steps).summarize(
        SummaryRequest(strategy=strategy, items=[summary_item(item)])
    )

    [sent] = runner.payloads["run-summarizer"]
    assert isinstance(sent, SummaryInput)
    assert sent.goal == strategy.goal_analysis
    assert [i.id for i in sent.items] == [item.id]
    assert result.patterns == [Pattern(pattern="AWS", item_ids=[item.id])]
    assert len(result.concerns) == 1
