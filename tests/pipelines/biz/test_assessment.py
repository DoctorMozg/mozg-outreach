from pathlib import Path

import pytest

from mozg_outreach.agents import AgentRunError, PromptLibrary
from mozg_outreach.domain import FieldDefinition, FieldSpec, FieldType, ItemCandidate
from mozg_outreach.pipelines.biz.assessment import BizAssessment
from mozg_outreach.pipelines.biz.assessment_models import (
    AssessmentContext,
    CompanyAssessment,
    FitJudgeInput,
    FitVerdict,
)
from mozg_outreach.pipelines.biz.discovery import dedup_companies
from mozg_outreach.pipelines.biz.models import ScoutResult, Strategy
from mozg_outreach.pipelines.fields import (
    FieldProposal,
    FieldResearchResult,
    ResearchedValue,
    accept_field_values,
)
from mozg_outreach.pipelines.steps import PipelineSteps
from tests.pipelines.biz.conftest import load_fixture
from tests.pipelines.conftest import REPO_PROMPTS, RecordedRunner, Scripted

SPEC = FieldSpec(
    fields=[
        FieldDefinition(id="cloud", label="Cloud", type=FieldType.LIST, description="d"),
        FieldDefinition(id="headcount", label="Headcount", type=FieldType.NUMBER, description="d"),
        FieldDefinition(id="uses_k8s", label="K8s", type=FieldType.BOOLEAN, description="d"),
        FieldDefinition(id="cto", label="CTO", type=FieldType.TEXT, description="d"),
    ]
)
URL = "https://acme.io/careers"


def researched(field_id: str, value: object, source_url: str | None = URL) -> ResearchedValue:
    return ResearchedValue.model_validate(
        {"field_id": field_id, "value": value, "source_url": source_url, "quote": "we run on AWS"}
    )


def test_keeps_typed_sourced_values_and_explains_drops() -> None:
    result = FieldResearchResult(
        values=[
            researched("cloud", ["AWS", "EKS"]),
            researched("headcount", "about 120"),
            researched("uses_k8s", True, source_url=None),
            researched("cto", None),
            researched("revenue", "10M"),
        ],
        concerns=["CTO not found on the team page."],
    )

    outcome = accept_field_values(result, SPEC)

    assert set(outcome.values) == {"cloud"}
    assert outcome.values["cloud"].source_url == URL
    assert outcome.values["cloud"].quote == "we run on AWS"
    assert outcome.values["cloud"].confidence == "unverified"
    assert outcome.concerns == [
        "CTO not found on the team page.",
        "Dropped 'headcount': 'about 120' is not a number.",
        "Dropped 'uses_k8s': no source page cited.",
        "Dropped value for unknown field 'revenue'.",
    ]


def test_bool_is_not_a_number() -> None:
    outcome = accept_field_values(
        FieldResearchResult(values=[researched("headcount", True)], concerns=[]), SPEC
    )
    assert outcome.values == {}


class TestAssess:
    @pytest.fixture
    def context(self, strategy: Strategy) -> AssessmentContext:
        return AssessmentContext(
            strategy=strategy, spec=load_fixture("field_proposal", FieldProposal).spec
        )

    @pytest.fixture
    def katana(self) -> ItemCandidate:
        companies = load_fixture("scout_results", list[ScoutResult])[0].companies
        return dedup_companies(companies)[0]

    async def run(
        self,
        script: dict[str, list[Scripted]],
        katana: ItemCandidate,
        context: AssessmentContext,
        tmp_path: Path,
    ) -> tuple[CompanyAssessment, RecordedRunner]:
        runner = RecordedRunner(script)
        steps = PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path)
        return await BizAssessment(steps).assess(katana, context), runner

    async def test_judges_fit_on_scouted_and_researched_facts(
        self, katana: ItemCandidate, context: AssessmentContext, tmp_path: Path
    ) -> None:
        script: dict[str, list[Scripted]] = {
            "field-researcher": [load_fixture("field_research", FieldResearchResult)],
            "fit-judge": [load_fixture("fit_verdict", FitVerdict)],
        }
        assessment, runner = await self.run(script, katana, context, tmp_path)

        assert assessment.fields["employee_count"].value == 100
        assert "uses_kubernetes" not in assessment.fields
        assert assessment.verdict is not None
        assert assessment.verdict.sector_match == "exact"
        payload = runner.payloads["fit-judge"][0]
        assert isinstance(payload, FitJudgeInput)
        assert payload.dossier is None
        assert {"domain", "evidence", "employee_count"} <= set(payload.facts)

    async def test_failed_research_still_judges_on_scouted_facts(
        self, katana: ItemCandidate, context: AssessmentContext, tmp_path: Path
    ) -> None:
        script: dict[str, list[Scripted]] = {
            "field-researcher": [
                AgentRunError("field-researcher", "error_max_turns", "out of turns")
            ],
            "fit-judge": [load_fixture("fit_verdict", FitVerdict)],
        }
        assessment, runner = await self.run(script, katana, context, tmp_path)

        assert assessment.fields == {}
        assert assessment.verdict is not None
        [concern] = assessment.concerns
        assert concern.startswith(f"Field research for {katana.title} failed")
        assert "out of turns" in concern
        payload = runner.payloads["fit-judge"][0]
        assert isinstance(payload, FitJudgeInput)
        assert set(payload.facts) == set(katana.fields)
