from datetime import timedelta
from pathlib import Path

from mozg_outreach.agents import AgentRunError, PromptLibrary
from mozg_outreach.pipelines.seeker.judge import Judgments
from mozg_outreach.pipelines.seeker.listings import SourcedListing
from mozg_outreach.pipelines.seeker.models import (
    AGENT_CHARS,
    GIG_WEIGHTS,
    JOB_WEIGHTS,
    OPENING_CHARS,
    Board,
    BoardPlan,
    ContactInput,
    JobContacts,
    JudgeInput,
    JudgeResult,
    ScoutInput,
    ScoutResult,
    SearchScope,
    SeekerBrief,
    SeekerStrategy,
    SeekerStrategyInput,
)
from mozg_outreach.pipelines.seeker.recruiter import RecruiterLookup, RecruiterTarget
from mozg_outreach.pipelines.seeker.search import SeekerSearch, load_boards
from mozg_outreach.pipelines.steps import PipelineSteps

from ..conftest import REPO_PROMPTS, RecordedRunner
from .conftest import LONG_POSTING, REQUIREMENTS, TODAY, board, judgment, listing, scouted

BOARDS = REPO_PROMPTS / "seeker" / "sources" / "boards.json"
GIG_URL = "https://malt.example/p/0"


def search(
    runner: RecordedRunner, tmp_path: Path, boards: list[Board] | None = None
) -> SeekerSearch:
    steps = PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path)
    return SeekerSearch(steps, boards or [])


def test_catalog_blocks_tos_sites_and_marks_gated_networks_as_guidance() -> None:
    boards = load_boards(BOARDS)
    blocked = {b.name for b in boards if not b.scrape_allowed and b.type != "guidance"}
    assert blocked == {"LinkedIn Jobs", "Indeed"}
    assert all(b.guidance_note for b in boards if b.type == "guidance")


class TestPlan:
    async def test_jobs_only_brief_drops_the_gig_plan_and_gig_engagements(
        self, tmp_path: Path, brief: SeekerBrief, strategy: SeekerStrategy
    ) -> None:
        brief = brief.model_copy(update={"gigs": None})
        runner = RecordedRunner({"seeker-strategist": [strategy]})

        planned = await search(runner, tmp_path).plan(brief, feedback="more Go")

        assert planned.gigs is None
        assert planned.preferences.engagement_types == ["full_time", "contract"]
        [payload] = runner.payloads["seeker-strategist"]
        assert isinstance(payload, SeekerStrategyInput)
        assert (payload.gigs, payload.feedback) == (None, "more Go")

    async def test_missing_plan_and_engagements_for_a_requested_kind_become_concerns(
        self, tmp_path: Path, brief: SeekerBrief, strategy: SeekerStrategy
    ) -> None:
        prefs = strategy.preferences.model_copy(update={"engagement_types": ["full_time"]})
        drafted = strategy.model_copy(
            update={"jobs": None, "preferences": prefs, "must_have_skills": ["Python", "Scala"]}
        )
        runner = RecordedRunner({"seeker-strategist": [drafted]})

        planned = await search(runner, tmp_path).plan(brief)

        assert planned.preferences.engagement_types == [
            "full_time",
            "project_gig",
            "contractor_placement",
            "consulting_advisory",
        ]
        assert planned.concerns == [
            "Must-have skills not found in the CV skills list: Scala",
            "The strategy has no plan for salaried jobs, so none are searched.",
            "No engagement type for freelance gigs; the defaults were used.",
        ]


class TestBoards:
    async def test_boards_follow_kinds_and_disciplines_and_split_guidance(
        self, tmp_path: Path, strategy: SeekerStrategy
    ) -> None:
        strategy = strategy.model_copy(update={"jobs": None})
        catalog = [
            board("Remotive", "job"),
            board("Malt", "gig", disciplines=["engineering"]),
            board("Dribbble", "gig", disciplines=["design"]),
            board("Datajobs", "gig", disciplines=["data"]),
            board("Spc", "gig", type="guidance", scrape_allowed=False, guidance_note="Apply."),
        ]
        added = [board("Malt", "gig"), board("Arbeitnow", "job"), board("Codementor", "gig")]
        runner = RecordedRunner(
            {"seeker-source-researcher": [BoardPlan(augmented=added, concerns=[])]}
        )

        choice = await search(runner, tmp_path, catalog).boards(strategy)

        assert [b.name for b in choice.scouted] == ["Malt", "Datajobs", "Codementor"]
        assert [b.name for b in choice.guidance] == ["Spc"]

    async def test_failed_research_keeps_the_catalog(
        self, tmp_path: Path, strategy: SeekerStrategy
    ) -> None:
        failure = AgentRunError("seeker-source-researcher", "error_max_turns", "timeout")
        runner = RecordedRunner({"seeker-source-researcher": [failure]})

        choice = await search(runner, tmp_path, [board("Remotive")]).boards(strategy)

        assert [b.name for b in choice.scouted] == ["Remotive"]


class TestScout:
    async def test_one_scout_per_board_and_kind_with_that_kinds_plan(
        self, tmp_path: Path, brief: SeekerBrief, strategy: SeekerStrategy
    ) -> None:
        boards = [board("Stepstone", "job", language_hint="de"), board("Wwr", "job", "gig")]
        found = ScoutResult(
            listings=[scouted().listing],
            queries_used=["q"],
            zero_results_verified=False,
            concerns=[],
        )
        failure = AgentRunError("seeker-scout", "error_during_execution", "rate limited")
        runner = RecordedRunner({"seeker-scout": [found, found, failure]})

        outcome = await search(runner, tmp_path).scout(strategy, boards, brief)

        payloads = [p for p in runner.payloads["seeker-scout"] if isinstance(p, ScoutInput)]
        sent = [(p.source.name, p.kind, p.max_results) for p in payloads]
        assert sent == [("Stepstone", "job", 50), ("Wwr", "job", 50), ("Wwr", "gig", 40)]
        assert payloads[0].queries == [
            "remote python backend engineer",
            "Backend Entwickler remote",
        ]
        assert payloads[2].target_titles == ["Backend Consultant"]
        assert payloads[2].title_aliases == ["API Engineer"]
        assert payloads[2].engagement_types == ["project_gig"]
        assert payloads[2].recency_days == 30
        assert [(s.source, s.kind) for s in outcome.listings] == [
            ("Stepstone", "job"),
            ("Wwr", "job"),
        ]
        assert outcome.concerns == [
            "Scout for Wwr (gig) failed: seeker-scout: run failed (error_during_execution): "
            "rate limited"
        ]

    async def test_a_board_label_in_the_company_slot_names_no_client(
        self, tmp_path: Path, brief: SeekerBrief, strategy: SeekerStrategy
    ) -> None:
        arc = board("Arc.dev", "gig", client_labels=["Arc Exclusive"])
        listings = [
            scouted(company=name).listing for name in ("arc exclusive", "Arc.dev", "AcmeCo")
        ]
        found = ScoutResult(
            listings=listings, queries_used=["q"], zero_results_verified=False, concerns=[]
        )
        runner = RecordedRunner({"seeker-scout": [found]})

        outcome = await search(runner, tmp_path).scout(strategy, [arc], brief)

        assert [s.listing.company for s in outcome.listings] == ["", "", "AcmeCo"]


class TestScreen:
    def test_each_kind_keeps_its_own_recency_and_limit(
        self, tmp_path: Path, brief: SeekerBrief, strategy: SeekerStrategy
    ) -> None:
        brief = brief.model_copy(update={"gigs": SearchScope(recency_days=30, limit=1)})
        old = (TODAY - timedelta(days=20)).isoformat()
        job = scouted(posted_date=old)
        gigs = [
            SourcedListing(
                source="Malt",
                kind="gig",
                listing=scouted(
                    url=f"https://malt.example/p/{n}", posted_date=old, title=title
                ).listing,
            )
            for n, title in enumerate(["Python API audit", "Postgres migration"])
        ]

        screening = search(RecordedRunner({}), tmp_path).screen(
            [job, *gigs], strategy, brief, TODAY
        )

        assert [(x.kind, x.url) for x in screening.kept] == [("gig", "https://malt.example/p/0")]
        assert [e.reason for e in screening.excluded] == ["stale"]
        assert screening.over_limit == {"job": 0, "gig": 1}

    def test_excluded_companies_gate_like_attributes(
        self, tmp_path: Path, brief: SeekerBrief, strategy: SeekerStrategy
    ) -> None:
        screening = search(RecordedRunner({}), tmp_path).screen(
            [scouted(company="Globex")], strategy, brief, TODAY
        )

        assert [(e.reason, e.detail) for e in screening.excluded] == [
            ("attribute_blocklist", "Globex")
        ]


class TestRank:
    def test_jobs_and_gigs_are_scored_with_their_own_weights(
        self, tmp_path: Path, strategy: SeekerStrategy
    ) -> None:
        job = listing()
        gig = listing(kind="gig", key=GIG_URL, url=GIG_URL)
        judged = Judgments(
            by_key={
                job.key: judgment(job.key),
                gig.key: judgment(gig.key, engagement_type="project_gig"),
            },
            concerns=[],
        )

        ranked = search(RecordedRunner({}), tmp_path).rank([job, gig], judged, strategy, TODAY)

        weights = {r.listing.kind: {a.axis: a.weight for a in r.score.axes} for r in ranked}
        assert weights == {"job": JOB_WEIGHTS.model_dump(), "gig": GIG_WEIGHTS.model_dump()}


class TestExcerpt:
    def test_short_description_is_unchanged(self) -> None:
        assert (
            listing(summary="Python on PostgreSQL.").excerpt(["Python"]) == "Python on PostgreSQL."
        )

    def test_long_description_keeps_the_opening_and_lines_naming_a_term(self) -> None:
        text = listing(summary=LONG_POSTING).excerpt(["PostgreSQL"], chars=1200)

        assert text.startswith(LONG_POSTING[:OPENING_CHARS])
        assert text.endswith(REQUIREMENTS)
        assert len(text) <= 1200

    def test_without_matching_lines_the_opening_is_cut(self) -> None:
        assert listing(summary=LONG_POSTING).excerpt(["Rust"], chars=1200) == LONG_POSTING[:1200]


class TestJudging:
    async def test_judge_reads_the_requirements_of_a_long_posting(
        self, tmp_path: Path, strategy: SeekerStrategy
    ) -> None:
        posting = listing(summary=LONG_POSTING * 3)
        runner = RecordedRunner(
            {"seeker-judge": [JudgeResult(judgments=[judgment(posting.key)], concerns=[])]}
        )

        await search(runner, tmp_path).judge([posting], strategy)

        [payload] = runner.payloads["seeker-judge"]
        assert isinstance(payload, JudgeInput)
        [sent] = payload.listings
        assert len(sent.summary) <= AGENT_CHARS
        assert REQUIREMENTS in sent.summary

    async def test_batches_of_ten_and_foreign_keys_dropped(
        self, tmp_path: Path, strategy: SeekerStrategy
    ) -> None:
        listings = [listing(key=f"https://a.example/{i}") for i in range(12)]
        first = JudgeResult(
            judgments=[judgment(f"https://a.example/{i}") for i in range(10)]
            + [judgment("https://other.example/x")],
            concerns=[],
        )
        second = JudgeResult(judgments=[judgment("https://a.example/10")], concerns=[])
        runner = RecordedRunner({"seeker-judge": [first, second]})
        judged = await search(runner, tmp_path).judge(listings, strategy)
        sizes = [
            len(p.listings) for p in runner.payloads["seeker-judge"] if isinstance(p, JudgeInput)
        ]
        assert sizes == [10, 2]
        assert len(judged.by_key) == 11
        assert judged.concerns == ["No judgment for: Senior Backend Engineer"]


async def test_recruiter_lookup_without_listing_keys_by_company(tmp_path: Path) -> None:
    found = JobContacts(
        named_contact=None,
        careers_email="jobs@acme.example",
        careers_email_source="https://acme.example/careers",
        linkedin_company=None,
        pattern_guesses=[],
        apply_url=None,
        concerns=[],
    )
    runner = RecordedRunner({"job-contact-finder": [found]})
    steps = PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path)
    contacts = await RecruiterLookup(steps).lookup(RecruiterTarget(company="Acme", region="DACH"))
    assert contacts.careers_email == "jobs@acme.example"
    [payload] = [p for p in runner.payloads["job-contact-finder"] if isinstance(p, ContactInput)]
    assert payload.listing.key == "company:acme"
    assert payload.listing.location_string == "DACH"
