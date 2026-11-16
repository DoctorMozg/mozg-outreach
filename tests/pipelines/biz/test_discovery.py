from pathlib import Path
from typing import Literal

import httpx
import pytest

from mozg_outreach.agents import AgentRunError, PromptLibrary
from mozg_outreach.domain import ProjectMode
from mozg_outreach.pipelines.biz.directories import Directories, KnownDirectory
from mozg_outreach.pipelines.biz.discovery import (
    BizDiscovery,
    DiscoveryError,
    company_key,
    dedup_companies,
    describe_target,
)
from mozg_outreach.pipelines.biz.models import (
    BizBrief,
    BriefReview,
    ClimateReport,
    ClimateSources,
    DirectorySource,
    ScoutedCompany,
    ScoutResult,
    SourceList,
    Strategy,
    StrategyInput,
)
from mozg_outreach.pipelines.fields import FieldProposal, FieldProposalInput, FieldProposer
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


def source(name: str, relevance: int, sector: str | None = None) -> DirectorySource:
    return DirectorySource(
        name=name,
        url=f"https://{name}.example",
        type="aggregator",
        estimated_count=None,
        relevance=relevance,
        access_notes="",
        search_queries_used=[],
        sector=sector,
    )


def with_sectors(strategy: Strategy, *sectors: str) -> Strategy:
    copy = strategy.model_copy(deep=True)
    copy.target_profile.sectors = list(sectors)
    return copy


def found(*names: str, queries: list[str] | None = None) -> ScoutResult:
    return ScoutResult(
        companies=[scouted(f"https://list.example/{n}", "page", name=n) for n in names],
        queries_used=queries or [],
        concerns=[],
    )


NOTHING = SourceList(sources=[], concerns=[])


async def test_recorded_chain_brief_to_deduped_candidates(
    tmp_path: Path, strategy: Strategy, sources: SourceList, scout_results: list[ScoutResult]
) -> None:
    brief = load("brief", BizBrief)
    other_sectors = len(strategy.target_profile.sectors) - 1
    empty = ScoutResult(companies=[], concerns=[])
    script: dict[str, list[Scripted]] = {
        "brief-reviewer": [load("brief_review", BriefReview)],
        "field-proposer": [load("field_proposal", FieldProposal)],
        "outreach-strategist": [strategy],
        "outreach-source-researcher": [sources, *[NOTHING] * other_sectors],
        "outreach-scout": [*scout_results, *[empty] * other_sectors],
    }
    runner = RecordedRunner(script)
    steps = PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path)
    d = BizDiscovery(steps)

    assert (await d.review_brief(brief)).complete
    proposal = await FieldProposer(steps).propose(
        FieldProposalInput(mode=ProjectMode.BIZ, brief=brief)
    )
    planned = await d.plan_strategy(
        StrategyInput(goal=brief.goal.goal, region="Estonia", brief=brief)
    )
    found = await d.find_sources(planned, region="Estonia", per_sector=2)
    outcome = await d.scout(planned, found.sources, region="Estonia", limit=6)

    assert "uses_kubernetes" in proposal.spec.by_id()
    by_key = {c.dedup_key: c for c in outcome.candidates}
    assert set(by_key) == {"katanamrp.com", "blackwall.com"}
    assert by_key["katanamrp.com"].fields["evidence"].value == "page"
    assert by_key["blackwall.com"].fields["evidence"].value == "page"
    assert by_key["blackwall.com"].fields["source_urls"].value == [
        scout_results[0].companies[1].source_url,
        "https://funding.tech.eu/companies/blackwall",
    ]
    assert f"{found.sources[0].name} (B2B SaaS): Directory returned HTTP 403." in outcome.concerns
    scout_payloads = [p.model_dump() for p in runner.payloads["outreach-scout"]]
    assert [p["count_limit"] for p in scout_payloads[:2]] == [5, 5]
    assert [p["source"] is None for p in scout_payloads] == [False] * 2 + [True] * other_sectors
    assert outcome.mix["B2B SaaS"] == 2


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
    async def test_each_sector_keeps_its_own_best_sources(
        self, tmp_path: Path, strategy: Strategy
    ) -> None:
        law = SourceList(
            sources=[source("rank", 9), source("low", 4), source("dir", 7)], concerns=[]
        )
        clinics = SourceList(sources=[source("portal", 6)], concerns=["Register needs a login."])
        runner = RecordedRunner({"outreach-source-researcher": [law, clinics]})

        kept = await discovery(runner, tmp_path).find_sources(
            with_sectors(strategy, "Law firms", "Clinics (multi-branch)"), "UAE", per_sector=1
        )

        assert [(s.name, s.sector) for s in kept.sources] == [
            ("rank", "Law firms"),
            ("portal", "Clinics (multi-branch)"),
        ]
        payloads = [p.model_dump() for p in runner.payloads["outreach-source-researcher"]]
        assert [p["sector"] for p in payloads] == ["Law firms", "Clinics (multi-branch)"]
        assert payloads[1]["target_description"].startswith("Clinics (multi-branch) in UAE")
        assert kept.concerns == ["Clinics: Register needs a login."]

    async def test_a_sector_without_sources_is_searched_instead(
        self, tmp_path: Path, strategy: Strategy
    ) -> None:
        failure = AgentRunError("outreach-source-researcher", "error_max_turns", "out of turns")
        low = SourceList(sources=[source("low", 3)], concerns=[])
        runner = RecordedRunner({"outreach-source-researcher": [low, failure]})

        kept = await discovery(runner, tmp_path).find_sources(
            with_sectors(strategy, "Law firms", "Clinics"), None, per_sector=2
        )

        assert kept.sources == []
        assert kept.concerns[0] == "No readable directory for Law firms; its scouts search the web."
        assert kept.concerns[1].startswith("Source research for Clinics failed")

    async def test_a_known_directory_joins_its_sector(
        self, tmp_path: Path, strategy: Strategy
    ) -> None:
        runner = RecordedRunner({"outreach-source-researcher": [NOTHING, NOTHING]})
        d = BizDiscovery(
            PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path),
            Directories(httpx.AsyncClient(), (FakeDirectory(),)),
        )

        kept = await d.find_sources(
            with_sectors(strategy, "Law firms", "Real estate brokerages"), "Dubai", per_sector=1
        )

        assert [(s.name, s.sector) for s in kept.sources] == [
            (FakeDirectory.name, "Real estate brokerages")
        ]


class FakeDirectory(KnownDirectory):
    name = "Fake brokers"
    url = "https://brokers.example/list"
    sector_words = ("real estate",)
    notes = "Test directory."

    async def read(self, client: httpx.AsyncClient, url: str, limit: int) -> list[ScoutedCompany]:
        return [scouted(url, "page", name=f"Broker {i}") for i in range(limit)]


class TestScout:
    async def test_one_failed_scout_becomes_a_concern(
        self, tmp_path: Path, strategy: Strategy, scout_results: list[ScoutResult]
    ) -> None:
        failure = AgentRunError("outreach-scout", "error_max_turns", "ran out of turns")
        runner = RecordedRunner({"outreach-scout": [failure, scout_results[0], found()]})
        one = with_sectors(strategy, "SaaS")
        outcome = await discovery(runner, tmp_path).scout(
            one, [source("a", 8, "SaaS"), source("b", 8, "SaaS")], region=None, limit=40
        )
        assert len(outcome.candidates) == 2
        assert outcome.concerns[0].startswith("Scout for a (SaaS) failed")
        counts = [p.model_dump()["count_limit"] for p in runner.payloads["outreach-scout"]]
        assert counts[:2] == [30, 30]

    async def test_every_scout_gets_only_its_sources_sector(
        self, tmp_path: Path, strategy: Strategy
    ) -> None:
        runner = RecordedRunner({"outreach-scout": [found("A1", "A2"), found("B1", "B2")]})
        await discovery(runner, tmp_path).scout(
            with_sectors(strategy, "Law firms", "Fintech"),
            [source("a", 8, "Law firms"), source("b", 8, "Fintech")],
            region=None,
            limit=4,
        )
        sectors = [p.model_dump()["sector"] for p in runner.payloads["outreach-scout"]]
        assert sectors == ["Law firms", "Fintech"]

    async def test_an_easy_sector_cannot_crowd_out_the_others(
        self, tmp_path: Path, strategy: Strategy
    ) -> None:
        law = found(*(f"Law {i}" for i in range(10)))
        runner = RecordedRunner({"outreach-scout": [law, found("Clinic 1", "Clinic 2"), found()]})

        outcome = await discovery(runner, tmp_path).scout(
            with_sectors(strategy, "Law firms", "Clinics"),
            [source("rank", 9, "Law firms"), source("portal", 6, "Clinics")],
            region=None,
            limit=6,
        )

        assert [c.title for c in outcome.candidates] == [
            "Law 0",
            "Clinic 1",
            "Law 1",
            "Clinic 2",
            "Law 2",
            "Law 3",
        ]
        assert outcome.mix == {"Law firms": 4, "Clinics": 2}

    async def test_a_short_sector_gets_a_search_round_that_skips_found_names(
        self, tmp_path: Path, strategy: Strategy
    ) -> None:
        runner = RecordedRunner(
            {"outreach-scout": [found("Clinic 1", queries=["clinics dubai"]), found("Clinic 2")]}
        )

        outcome = await discovery(runner, tmp_path).scout(
            with_sectors(strategy, "Clinics"),
            [source("portal", 6, "Clinics")],
            region=None,
            limit=4,
        )

        retry = runner.payloads["outreach-scout"][1].model_dump()
        assert retry["source"] is None
        assert retry["exclude_names"] == ["Clinic 1"]
        assert retry["queries_tried"] == ["clinics dubai"]
        assert [c.title for c in outcome.candidates] == ["Clinic 1", "Clinic 2"]

    async def test_an_empty_web_search_is_not_repeated(
        self, tmp_path: Path, strategy: Strategy
    ) -> None:
        runner = RecordedRunner({"outreach-scout": [found("Law 1"), found()]})

        await discovery(runner, tmp_path).scout(
            with_sectors(strategy, "Law firms", "Clinics"),
            [source("rank", 9, "Law firms")],
            region=None,
            limit=2,
        )

        payloads = [p.model_dump() for p in runner.payloads["outreach-scout"]]
        assert [(p["sector"], p["source"] is None) for p in payloads] == [
            ("Law firms", False),
            ("Clinics", True),
        ]

    async def test_a_known_directory_is_read_without_an_agent(
        self, tmp_path: Path, strategy: Strategy
    ) -> None:
        runner = RecordedRunner({})
        d = BizDiscovery(
            PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path),
            Directories(httpx.AsyncClient(), (FakeDirectory(),)),
        )
        known = FakeDirectory().source("Real estate")

        outcome = await d.scout(
            with_sectors(strategy, "Real estate"), [known], region=None, limit=3
        )

        assert [c.title for c in outcome.candidates] == ["Broker 0", "Broker 1", "Broker 2"]
        assert "outreach-scout" not in runner.payloads

    async def test_all_scouts_failing_stops(self, tmp_path: Path, strategy: Strategy) -> None:
        failure = AgentRunError("outreach-scout", "error_during_execution", "crash")
        runner = RecordedRunner({"outreach-scout": [failure, failure]})
        with pytest.raises(DiscoveryError, match="all 2 scouts failed"):
            await discovery(runner, tmp_path).scout(
                with_sectors(strategy, "SaaS"),
                [source("a", 8, "SaaS"), source("b", 8, "SaaS")],
                region=None,
                limit=5,
            )


def scouted(
    url: str,
    evidence: Literal["page", "search_snippet"],
    name: str = "Acme",
    domain: str | None = None,
) -> ScoutedCompany:
    return ScoutedCompany(
        name=name,
        domain=domain,
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


class TestFirmNames:
    @pytest.mark.parametrize(
        ("one", "other"),
        [
            ("Afridi & Angell LLP", "Afridi & Angell Legal Consultants"),
            ("Charles Russell Speechlys", "Charles Russell Speechlys LLP"),
            ("Galadari", "Galadari Advocates & Legal Consultants"),
            ("Stephenson Harwood Middle East LLP", "Stephenson Harwood"),
            ("DWF (Middle East) LLP", "DWF"),
            ("Al Tamimi & Company", "Al Tamimi"),
        ],
    )
    def test_variants_of_one_firm_share_a_key(self, one: str, other: str) -> None:
        assert company_key(one) == company_key(other)

    def test_different_firms_keep_apart(self) -> None:
        assert company_key("Jassim Al Suwaidi Advocates") != company_key("Alsuwaidi & Company")

    def test_a_domain_row_merges_into_the_same_firm_by_name(self) -> None:
        rows = [
            scouted("https://a.example/1", "page", name="Hadef & Partners"),
            scouted("https://b.example/2", "page", name="Hadef", domain="hadefpartners.com"),
        ]
        [candidate] = dedup_companies(rows)
        assert candidate.fields["domain"].value == "hadefpartners.com"
        assert candidate.fields["source_urls"].value == [
            "https://a.example/1",
            "https://b.example/2",
        ]


def test_region_overrides_profile_geography(strategy: Strategy) -> None:
    assert "in Latvia" in describe_target(strategy, "Latvia")
    assert "Latvia" not in describe_target(strategy, None)
