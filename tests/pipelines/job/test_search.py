from pathlib import Path

import pytest

from mozg_outreach.agents import AgentRunError, PromptLibrary
from mozg_outreach.pipelines.job.models import (
    JobBrief,
    JobScoutInput,
    JobScoutResult,
    JobSource,
    JobStrategy,
    JudgeInput,
    JudgeResult,
    SourcePlan,
)
from mozg_outreach.pipelines.job.search import JobSearch, Judgments, load_canonical
from mozg_outreach.pipelines.steps import PipelineSteps

from ..conftest import REPO_PROMPTS, RecordedRunner
from .conftest import TODAY, judgment, listing, scouted

CANONICAL = REPO_PROMPTS / "job" / "sources" / "job_canonical.json"


def source(name: str, *, allowed: bool = True, language: str = "en") -> JobSource:
    return JobSource(
        name=name,
        url=f"https://{name.lower()}.example/",
        type="global",
        tier="augmented",
        relevance_score=7,
        scrape_hints="",
        access_notes="",
        language_hint=language,
        scrape_allowed=allowed,
    )


def scout_result(*urls: str) -> JobScoutResult:
    return JobScoutResult(
        listings=[scouted(url=u).listing for u in urls],
        queries_used=["q"],
        zero_results_verified=not urls,
        concerns=[],
    )


def search(runner: RecordedRunner, tmp_path: Path, canonical: list[JobSource]) -> JobSearch:
    return JobSearch(PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path), canonical)


def test_canonical_boards_load_and_exclude_tos_sites() -> None:
    boards = load_canonical(CANONICAL)
    blocked = {b.name for b in boards if not b.scrape_allowed}
    assert blocked == {"LinkedIn Jobs", "Indeed"}


class TestPlanning:
    async def test_must_have_missing_from_cv_becomes_a_concern(
        self, tmp_path: Path, strategy: JobStrategy, brief: JobBrief
    ) -> None:
        drafted = strategy.model_copy(update={"must_have_skills": ["Python", "Scala"]})
        runner = RecordedRunner({"job-strategist": [drafted]})
        planned = await search(runner, tmp_path, []).plan(brief, feedback="more Go roles")
        assert planned.concerns == ["Must-have skills not found in the CV skills list: Scala"]
        assert runner.payloads["job-strategist"][0].model_dump()["feedback"] == "more Go roles"

    async def test_sources_drop_blocked_and_duplicate_boards(
        self, tmp_path: Path, strategy: JobStrategy
    ) -> None:
        canonical = [source("Alpha"), source("Blocked", allowed=False)]
        plan = SourcePlan(augmented=[source("Alpha"), source("Beta")], concerns=[])
        runner = RecordedRunner({"job-source-researcher": [plan]})
        chosen = await search(runner, tmp_path, canonical).sources(strategy)
        assert [s.name for s in chosen] == ["Alpha", "Beta"]

    async def test_failed_research_falls_back_to_canonical(
        self, tmp_path: Path, strategy: JobStrategy
    ) -> None:
        runner = RecordedRunner(
            {
                "job-source-researcher": [
                    AgentRunError("job-source-researcher", "error_max_turns", "timeout")
                ]
            }
        )
        chosen = await search(runner, tmp_path, [source("Alpha")]).sources(strategy)
        assert [s.name for s in chosen] == ["Alpha"]


class TestScouting:
    async def test_failed_scout_is_a_concern_and_queries_follow_language(
        self, tmp_path: Path, strategy: JobStrategy, brief: JobBrief
    ) -> None:
        boards = [source("Alpha"), source("Stepstone", language="de"), source("Gamma")]
        runner = RecordedRunner(
            {
                "job-scout": [
                    scout_result("https://a.example/1"),
                    scout_result(),
                    AgentRunError("job-scout", "error_during_execution", "rate limited"),
                ]
            }
        )
        outcome = await search(runner, tmp_path, []).scout(strategy, boards, brief)
        assert [i.source for i in outcome.listings] == ["Alpha"]
        assert outcome.empty_sources == ["Stepstone"]
        assert outcome.concerns == [
            "Scout for Gamma failed: job-scout: run failed (error_during_execution): rate limited"
        ]
        payloads = [p for p in runner.payloads["job-scout"] if isinstance(p, JobScoutInput)]
        assert payloads[1].queries == [
            "remote python backend engineer",
            "Backend Entwickler remote",
        ]
        assert payloads[0].max_results == 34


class TestJudging:
    async def test_batches_of_ten_and_foreign_keys_dropped(
        self, tmp_path: Path, strategy: JobStrategy
    ) -> None:
        listings = [listing(key=f"https://a.example/{i}") for i in range(12)]
        first = JudgeResult(
            judgments=[judgment(f"https://a.example/{i}") for i in range(10)]
            + [judgment("https://other.example/x")],
            concerns=[],
        )
        second = JudgeResult(judgments=[judgment("https://a.example/10")], concerns=[])
        runner = RecordedRunner({"job-listing-judge": [first, second]})
        judged = await search(runner, tmp_path, []).judge(listings, strategy)
        sizes = [
            len(p.listings)
            for p in runner.payloads["job-listing-judge"]
            if isinstance(p, JudgeInput)
        ]
        assert sizes == [10, 2]
        assert len(judged.by_key) == 11
        assert judged.concerns == ["No judgment for: Senior Backend Engineer"]

    @pytest.mark.parametrize("hits", [[], ["on-call"]])
    def test_rank_orders_by_total(
        self, tmp_path: Path, strategy: JobStrategy, hits: list[str]
    ) -> None:
        good = listing(key="https://a.example/good", summary="Python PostgreSQL Go Kafka")
        weak = listing(key="https://a.example/weak", summary="Python MySQL")
        judgments = Judgments(
            by_key={
                good.key: judgment(good.key, deal_breaker_hits=hits),
                weak.key: judgment(weak.key),
            },
            concerns=[],
        )
        ranked = search(RecordedRunner({}), tmp_path, []).rank(
            [weak, good], judgments, strategy, TODAY
        )
        order = [r.listing.key for r in ranked]
        assert order == ([good.key, weak.key] if not hits else [weak.key, good.key])
