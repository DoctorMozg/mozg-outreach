from pathlib import Path
from typing import Literal

import pytest

from mozg_outreach.agents import AgentRunError, PromptLibrary
from mozg_outreach.pipelines.biz.discovery import (
    BizDiscovery,
    DiscoveryError,
    dedup_companies,
    describe_target,
)
from mozg_outreach.pipelines.biz.models import (
    BizBrief,
    BriefReview,
    ClimateReport,
    ClimateSources,
    DirectorySource,
    FieldProposal,
    ScoutedCompany,
    ScoutResult,
    SourceList,
    Strategy,
    StrategyInput,
)
from mozg_outreach.pipelines.steps import PipelineSteps
from tests.pipelines.biz.conftest import load_fixture as load
from tests.pipelines.conftest import REPO_PROMPTS, RecordedRunner, Scripted


@pytest.fixture(scope="module")
def sources() -> SourceList:
    return load("sources", SourceList)


@pytest.fixture(scope="module")
def scout_results() -> list[ScoutResult]:
    return load("scout_results", list[ScoutResult])


def discovery(runner: RecordedRunner, tmp_path: Path) -> BizDiscovery:
    return BizDiscovery(PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path))


def source(name: str, relevance: int) -> DirectorySource:
    return DirectorySource(
        name=name,
        url=f"https://{name}.example",
        type="aggregator",
        estimated_count=None,
        relevance=relevance,
        access_notes="",
        search_queries_used=[],
    )


async def test_recorded_chain_brief_to_deduped_candidates(
    tmp_path: Path, strategy: Strategy, sources: SourceList, scout_results: list[ScoutResult]
) -> None:
    brief = load("brief", BizBrief)
    script: dict[str, list[Scripted]] = {
        "brief-reviewer": [load("brief_review", BriefReview)],
        "field-proposer": [load("field_proposal", FieldProposal)],
        "outreach-strategist": [strategy],
        "outreach-source-researcher": [sources],
        "outreach-scout": list(scout_results),
    }
    runner = RecordedRunner(script)
    steps = PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path)
    d = BizDiscovery(steps)

    assert (await d.review_brief(brief)).complete
    proposal = await d.propose_fields(brief, None)
    planned = await d.plan_strategy(
        StrategyInput(goal=brief.goal.goal, region="Estonia", brief=brief)
    )
    found = await d.find_sources(planned, region="Estonia")
    outcome = await d.scout(planned, found.sources[:2], region="Estonia", limit=6)

    assert "uses_kubernetes" in proposal.spec.by_id()
    by_key = {c.dedup_key: c for c in outcome.candidates}
    assert set(by_key) == {"katanamrp.com", "blackwall.com"}
    assert by_key["katanamrp.com"].fields["evidence"].value == "page"
    assert by_key["blackwall.com"].fields["evidence"].value == "page"
    assert by_key["blackwall.com"].fields["source_urls"].value == [
        scout_results[0].companies[1].source_url,
        "https://funding.tech.eu/companies/blackwall",
    ]
    assert outcome.concerns == [f"{found.sources[0].name}: Directory returned HTTP 403."]
    assert steps.cost_usd == pytest.approx(0.6)
    scout_payloads = runner.payloads["outreach-scout"]
    assert [p.model_dump()["count_limit"] for p in scout_payloads] == [10, 10]


class TestClimate:
    async def test_runs_finder_then_researcher_with_sources(self, tmp_path: Path) -> None:
        found = load("climate_sources", ClimateSources)
        report = load("climate_report", ClimateReport)
        runner = RecordedRunner(
            {
                "outreach-climate-source-finder": [found],
                "outreach-climate-researcher": [report],
            }
        )
        brief = load("brief", BizBrief)

        result = await discovery(runner, tmp_path).research_climate(brief, "Estonia")

        researcher_input = runner.payloads["outreach-climate-researcher"][0].model_dump()
        assert researcher_input["sources"][0]["url"] == found.sources[0].url
        assert researcher_input["region"] == "Estonia"
        assert result.signals.economic[0].source_url.startswith("https://")
        assert result.concerns == [*found.concerns, *report.concerns]

    async def test_no_sources_stops_before_researcher(self, tmp_path: Path) -> None:
        runner = RecordedRunner(
            {"outreach-climate-source-finder": [ClimateSources(sources=[], concerns=[])]}
        )
        with pytest.raises(DiscoveryError, match="no climate sources"):
            await discovery(runner, tmp_path).research_climate(load("brief", BizBrief), "Estonia")
        assert "outreach-climate-researcher" not in runner.payloads


class TestSources:
    async def test_keeps_relevant_sorted_and_capped(
        self, tmp_path: Path, strategy: Strategy
    ) -> None:
        many = [source(f"s{i}", 6) for i in range(12)] + [source("low", 4), source("top", 10)]
        runner = RecordedRunner(
            {"outreach-source-researcher": [SourceList(sources=many, concerns=[])]}
        )
        kept = await discovery(runner, tmp_path).find_sources(strategy, region=None)
        assert len(kept.sources) == 10
        assert kept.sources[0].name == "top"
        assert all(s.relevance >= 5 for s in kept.sources)

    async def test_no_relevant_source_stops(self, tmp_path: Path, strategy: Strategy) -> None:
        runner = RecordedRunner(
            {"outreach-source-researcher": [SourceList(sources=[source("low", 3)], concerns=[])]}
        )
        with pytest.raises(DiscoveryError, match="relevance"):
            await discovery(runner, tmp_path).find_sources(strategy, region=None)


class TestScout:
    async def test_one_failed_scout_becomes_a_concern(
        self, tmp_path: Path, strategy: Strategy, scout_results: list[ScoutResult]
    ) -> None:
        failure = AgentRunError("outreach-scout", "error_max_turns", "ran out of turns")
        runner = RecordedRunner({"outreach-scout": [failure, scout_results[0]]})
        outcome = await discovery(runner, tmp_path).scout(
            strategy, [source("a", 8), source("b", 8)], region=None, limit=40
        )
        assert len(outcome.candidates) == 2
        assert outcome.concerns[0].startswith("Scout for a failed")
        assert [p.model_dump()["count_limit"] for p in runner.payloads["outreach-scout"]] == [
            20,
            20,
        ]

    async def test_all_scouts_failing_stops(self, tmp_path: Path, strategy: Strategy) -> None:
        failure = AgentRunError("outreach-scout", "error_during_execution", "crash")
        runner = RecordedRunner({"outreach-scout": [failure, failure]})
        with pytest.raises(DiscoveryError, match="all 2 scouts failed"):
            await discovery(runner, tmp_path).scout(
                strategy, [source("a", 8), source("b", 8)], region=None, limit=5
            )


def scouted(url: str, evidence: Literal["page", "search_snippet"]) -> ScoutedCompany:
    return ScoutedCompany(
        name="Acme",
        domain="acme.io",
        sector=None,
        location=None,
        founded=None,
        description=None,
        source_url=url,
        evidence_quote="Acme is listed",
        evidence=evidence,
    )


class TestEvidence:
    def test_snippet_only_company_is_kept_and_marked(self) -> None:
        [candidate] = dedup_companies([scouted("https://a.example/1", "search_snippet")])
        assert candidate.fields["evidence"].value == "search_snippet"

    def test_page_row_upgrades_snippet_row(self) -> None:
        rows = [
            scouted("https://a.example/1", "search_snippet"),
            scouted("https://b.example/2", "page"),
        ]
        [candidate] = dedup_companies(rows)
        assert candidate.fields["evidence"].value == "page"
        assert candidate.fields["evidence"].source_url == "https://b.example/2"

    def test_snippet_row_does_not_downgrade_page_row(self) -> None:
        rows = [
            scouted("https://a.example/1", "page"),
            scouted("https://b.example/2", "search_snippet"),
        ]
        [candidate] = dedup_companies(rows)
        assert candidate.fields["evidence"].value == "page"


def test_region_overrides_profile_geography(strategy: Strategy) -> None:
    assert "in Latvia" in describe_target(strategy, "Latvia")
    assert "Latvia" not in describe_target(strategy, None)
