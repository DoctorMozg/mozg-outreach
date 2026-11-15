import pytest
from sqlalchemy import select

from mozg_outreach.cvs.models import CV_CHOICE, CvChoice, CvRating, CvRatings, CvSummary
from mozg_outreach.domain import BriefFact, FieldValue, ItemKind, RunStatus
from mozg_outreach.letters.models import LetterBrief, LetterDraft, Revision
from mozg_outreach.naturalize.models import NaturalizeResult
from mozg_outreach.pipelines.seeker.models import (
    BoardPlan,
    JudgeResult,
    ScoutedListing,
    ScoutResult,
    SeekerStrategy,
)
from mozg_outreach.runs.flow import GateAnswer
from mozg_outreach.runs.seeker import (
    IDENTIFY_MIN_SCORE,
    WEB_SEARCH,
    SeekerSearchState,
    listing_of,
)
from mozg_outreach.sources.models import SourceItem
from mozg_outreach.storage.models import Cv, Draft, Item, Project, Run, SourceConfig
from tests.pipelines.conftest import Scripted
from tests.pipelines.leads.conftest import company, identity
from tests.pipelines.seeker.conftest import CV_TEXT, GIG_URL, SCOPE, judgment
from tests.pipelines.seeker.test_letters import BODY

from .conftest import FEED_URL, NOW, Harness

OPTIONS = {"max_boards": 1, "research_fields_top": 0, "summary": False}
# Proposals get a longer word budget than cover letters.
PROPOSAL_BODY = " ".join(["word"] * 300)
OTHER_URL = "https://boards.greenhouse.io/acme/jobs/2"
NOTICE = BriefFact(label="Notice period", value="One month")


def scouted_gig() -> ScoutedListing:
    return ScoutedListing(
        title="Payments API redesign",
        company="AcmeCo",
        location_string="Remote (EU)",
        remote_status_raw="Remote",
        url=GIG_URL,
        posted_date=NOW.date().isoformat(),
        salary_string="EUR 700/day",
        summary_snippet=SCOPE,
        duration_raw="8 weeks",
        application_method_raw="Apply via Malt.",
    )


def scouted(*listings: ScoutedListing) -> ScoutResult:
    return ScoutResult(
        listings=list(listings), queries_used=["q"], zero_results_verified=False, concerns=[]
    )


def letter(salutation: str, body: str) -> dict[str, list[Scripted]]:
    """The project's neutral tone signs off with "Best regards"."""
    draft = LetterDraft(subject=None, body=body, hook_used="", angle_used="", concerns=[])
    framed = f"{salutation}\n\n{body}\n\nBest regards,\nStan"
    natural = NaturalizeResult(text=framed, changes=[], specificity_needed=[], concerns=[])
    return {"expert-copywriter": [draft], "expert-naturalizer": [natural]}


async def finish(harness: Harness, run_id: int) -> Run:
    await harness.manager.wait(run_id)
    with harness.db.session() as session:
        run = session.get(Run, run_id)
        assert run is not None
        session.expunge(run)
    return run


async def test_leads_a_deal_breaker_ruled_out_are_not_identified(
    harness: Harness, strategy: SeekerStrategy
) -> None:
    project_id = harness.seeker_project()
    harness.feed.extra.append(
        SourceItem(
            source="greenhouse",
            source_item_id="acme/2",
            url=OTHER_URL,
            kind="job",
            title="Backend Engineer, on-call",
            raw_text="Python and PostgreSQL services with a weekly on-call. Remote within the EU.",
            raw_payload={},
            published_at=NOW,
            fetched_at=NOW,
            org_name="Acme",
            location="Remote - EU",
        )
    )
    blocked = judgment(OTHER_URL, deal_breaker_hits=["on-call"])
    harness.script(
        {
            "seeker-strategist": [strategy],
            "seeker-judge": [JudgeResult(judgments=[judgment(FEED_URL), blocked], concerns=[])],
            "lead-identifier": [identity(company())],
        }
    )
    search = harness.manager.start(project_id, "seeker.search", {"options": OPTIONS})
    await harness.manager.wait(search)
    harness.manager.answer_gate(search, GateAnswer(approve=True))
    done = await finish(harness, search)

    assert done.status == RunStatus.COMPLETE, done.error
    [sent] = harness.runner.payloads["lead-identifier"]
    assert sent.model_dump()["url"] == FEED_URL
    with harness.db.session() as session:
        assert len(session.scalars(select(Item)).all()) == 2, "the blocked lead is still reported"


async def test_one_search_reports_jobs_and_gigs_and_drafts_a_letter_for_each(
    harness: Harness, strategy: SeekerStrategy
) -> None:
    project_id = harness.seeker_project(gigs=True, facts=[NOTICE])
    with harness.db.session() as session:
        session.add(SourceConfig(project_id=project_id, kind=WEB_SEARCH))
    harness.script(
        {
            "seeker-strategist": [strategy],
            "seeker-source-researcher": [BoardPlan(augmented=[], concerns=[])],
            # The job board scouts first and finds nothing new; the feed brings the job.
            "seeker-scout": [scouted(), scouted(scouted_gig())],
            "seeker-judge": [
                JudgeResult(
                    judgments=[
                        judgment(FEED_URL),
                        judgment(GIG_URL, engagement_type="project_gig"),
                    ],
                    concerns=[],
                )
            ],
            "lead-identifier": [identity(company()), identity(None)],
        }
    )
    search = harness.manager.start(project_id, "seeker.search", {"options": OPTIONS})
    await harness.manager.wait(search)
    harness.manager.answer_gate(search, GateAnswer(approve=True))
    done = await finish(harness, search)
    assert done.status == RunStatus.COMPLETE, done.error

    [plan] = harness.runner.payloads["seeker-strategist"]
    assert plan.model_dump()["facts"] == [NOTICE.model_dump()]
    scouts = harness.runner.payloads["seeker-scout"]
    assert [p.model_dump()["kind"] for p in scouts] == ["job", "gig"]
    with harness.db.session() as session:
        items = {i.kind: i for i in session.scalars(select(Item))}
        assert set(items) == {ItemKind.JOB, ItemKind.GIG}
        assert all(i.score_total is not None for i in items.values())
        gig_fields = items[ItemKind.GIG].fields
        assert FieldValue.model_validate(gig_fields["pay"]).value == "EUR 700/day"
        assert FieldValue.model_validate(gig_fields["duration"]).value == "8 weeks"
        for item in items.values():
            item.selected = True
        ids = {kind: item.id for kind, item in items.items()}

    letters = {
        ItemKind.JOB: letter("Dear Hiring Team,", BODY),
        ItemKind.GIG: letter("Hi there,", PROPOSAL_BODY),
    }
    for kind, script in letters.items():
        harness.script(script)
        run_id = harness.manager.start(project_id, "seeker.letters", {"item_id": ids[kind]})
        drafted = await finish(harness, run_id)
        assert drafted.status == RunStatus.COMPLETE, drafted.error

    for sent in harness.runner.payloads["expert-copywriter"]:
        assert isinstance(sent, LetterBrief)
        assert sent.sender_background == f"{CV_TEXT}\n\nMore facts:\n- Notice period: One month"
    with harness.db.session() as session:
        channels = {d.item_id: d.channel for d in session.scalars(select(Draft))}
    assert channels == {ids[ItemKind.JOB]: "cover_letter", ids[ItemKind.GIG]: "proposal"}


async def test_leads_scoring_under_the_floor_stay_in_the_report_but_are_not_identified(
    harness: Harness, strategy: SeekerStrategy
) -> None:
    project_id = harness.seeker_project()
    harness.feed.extra.append(
        SourceItem(
            source="greenhouse",
            source_item_id="acme/2",
            url=OTHER_URL,
            kind="job",
            title="Platform Engineer, internal tools",
            raw_text="Python and PostgreSQL services. Remote within the EU.",
            raw_payload={},
            published_at=NOW,
            fetched_at=NOW,
            org_name="Acme",
            location="Remote - EU",
        )
    )
    weak = judgment(OTHER_URL, seniority="intern", counterparty="thin", scope_clarity="vague")
    harness.script(
        {
            "seeker-strategist": [strategy],
            "seeker-judge": [JudgeResult(judgments=[judgment(FEED_URL), weak], concerns=[])],
            "lead-identifier": [identity(company())],
        }
    )
    search = harness.manager.start(project_id, "seeker.search", {"options": OPTIONS})
    await harness.manager.wait(search)
    harness.manager.answer_gate(search, GateAnswer(approve=True))
    done = await finish(harness, search)

    assert done.status == RunStatus.COMPLETE, done.error
    with harness.db.session() as session:
        totals = {listing_of(i)[0].url: i.score_total for i in session.scalars(select(Item))}
    assert len(totals) == 2, "the weak lead is still reported"
    assert (totals[FEED_URL] or 0) >= IDENTIFY_MIN_SCORE > (totals[OTHER_URL] or 0)
    [sent] = harness.runner.payloads["lead-identifier"]
    assert sent.model_dump()["url"] == FEED_URL


async def test_boards_an_enabled_feed_already_covers_are_not_scouted(
    harness: Harness, strategy: SeekerStrategy
) -> None:
    project_id = harness.seeker_project()
    with harness.db.session() as session:
        session.add(SourceConfig(project_id=project_id, kind=WEB_SEARCH))
        session.add(SourceConfig(project_id=project_id, kind="remoteok", config={}))
    harness.script(
        {
            "seeker-strategist": [strategy],
            "seeker-source-researcher": [BoardPlan(augmented=[], concerns=[])],
            "seeker-scout": [scouted() for _ in range(30)],
            "seeker-judge": [JudgeResult(judgments=[judgment(FEED_URL)], concerns=[])],
            "lead-identifier": [identity(company())],
        }
    )
    options = {**OPTIONS, "max_boards": 15}
    search = harness.manager.start(project_id, "seeker.search", {"options": options})
    await harness.manager.wait(search)
    harness.manager.answer_gate(search, GateAnswer(approve=True))
    done = await finish(harness, search)

    assert done.status == RunStatus.COMPLETE, done.error
    names = {b.name for b in SeekerSearchState.model_validate(done.state).boards}
    assert {"Wellfound", "Lever ATS (via Google)"} <= names
    assert names.isdisjoint({"RemoteOK", "Greenhouse ATS"})


class TestReuseLastStrategy:
    async def approved_search(self, harness: Harness, strategy: SeekerStrategy) -> tuple[int, int]:
        """A finished first search whose strategy the user edited at the gate."""
        project_id = harness.seeker_project()
        judged = JudgeResult(judgments=[judgment(FEED_URL)], concerns=[])
        harness.script(
            {
                "seeker-strategist": [strategy, strategy],
                "seeker-judge": [judged, judged],
                "lead-identifier": [identity(), identity()],
            }
        )
        run_id = harness.manager.start(project_id, "seeker.search", {})
        await harness.manager.wait(run_id)
        edited = strategy.model_copy(update={"must_have_skills": ["Python", "Go"]})
        revised = edited.model_dump(mode="json")
        harness.manager.answer_gate(run_id, GateAnswer(approve=True, revised=revised))
        assert (await finish(harness, run_id)).status == RunStatus.COMPLETE
        return project_id, run_id

    async def test_next_search_reuses_the_approved_strategy_without_asking(
        self, harness: Harness, strategy: SeekerStrategy
    ) -> None:
        project_id, first = await self.approved_search(harness, strategy)

        options: dict[str, object] = {"options": {"summary": False}}
        again = await finish(harness, harness.manager.start(project_id, "seeker.search", options))

        assert again.status == RunStatus.COMPLETE, again.pending_gate
        state = SeekerSearchState.model_validate(again.state)
        assert state.reused_from == first
        assert state.strategy is not None
        assert state.strategy.must_have_skills == ["Python", "Go"]
        assert len(harness.runner.payloads["seeker-strategist"]) == 1

    @pytest.mark.parametrize("change", ["brief", "opt_out"])
    async def test_a_changed_brief_or_opting_out_plans_again(
        self, harness: Harness, strategy: SeekerStrategy, change: str
    ) -> None:
        project_id, _ = await self.approved_search(harness, strategy)
        options = {"reuse_last_strategy": False} if change == "opt_out" else {}
        if change == "brief":
            with harness.db.session() as session:
                project = session.get(Project, project_id)
                assert project is not None
                assert project.brief is not None
                project.brief = {**project.brief, "preferences": "Remote worldwide"}

        again = await finish(
            harness, harness.manager.start(project_id, "seeker.search", {"options": options})
        )

        assert (again.status, again.phase) == (RunStatus.AWAITING_GATE, "approve_strategy")
        assert SeekerSearchState.model_validate(again.state).reused_from is None
        assert len(harness.runner.payloads["seeker-strategist"]) == 2


async def search_one_job(harness: Harness, strategy: SeekerStrategy) -> tuple[int, int]:
    """A finished search that reported the feed's one job, selected; project and item ids."""
    project_id = harness.seeker_project()
    harness.script(
        {
            "seeker-strategist": [strategy],
            "seeker-judge": [JudgeResult(judgments=[judgment(FEED_URL)], concerns=[])],
            "lead-identifier": [identity(company())],
        }
    )
    search = harness.manager.start(project_id, "seeker.search", {"options": OPTIONS})
    await harness.manager.wait(search)
    harness.manager.answer_gate(search, GateAnswer(approve=True))
    assert (await finish(harness, search)).status == RunStatus.COMPLETE
    with harness.db.session() as session:
        job = session.scalars(select(Item)).one()
        job.selected = True
        return project_id, job.id


async def test_a_draft_is_rewritten_with_the_users_suggestion(
    harness: Harness, strategy: SeekerStrategy
) -> None:
    project_id, job_id = await search_one_job(harness, strategy)
    harness.script(letter("Dear Hiring Team,", BODY))
    first = harness.manager.start(project_id, "seeker.letters", {"item_id": job_id})
    assert (await finish(harness, first)).status == RunStatus.COMPLETE
    with harness.db.session() as session:
        draft = session.scalars(select(Draft)).one()
        draft_id, written = draft.id, draft.body

    harness.script(letter("Dear Hiring Team,", BODY))
    ask = {"draft_id": draft_id, "suggestion": "Mention the notice period."}
    second = harness.manager.start(
        project_id, "seeker.letters", {"item_id": job_id, "revision": ask}
    )
    assert (await finish(harness, second)).status == RunStatus.COMPLETE

    first_brief, revised_brief = harness.runner.payloads["expert-copywriter"]
    assert isinstance(first_brief, LetterBrief)
    assert isinstance(revised_brief, LetterBrief)
    assert first_brief.revision is None
    assert revised_brief.revision == Revision(
        previous_text=written, suggestion="Mention the notice period."
    )
    with harness.db.session() as session:
        versions = [d.version for d in session.scalars(select(Draft).order_by(Draft.id))]
    assert versions == [1, 2]


class TestCvChoice:
    TEXTS = ("Data engineer CV: Airflow, dbt and Spark.", "Backend CV: Python, FastAPI, Kafka.")

    async def searched_job(self, harness: Harness, strategy: SeekerStrategy) -> int:
        project_id, _ = await search_one_job(harness, strategy)
        with harness.db.session() as session:
            for n, text in enumerate(self.TEXTS):
                summary = CvSummary(
                    language="en",
                    headline=text.split(":")[0],
                    seniority="senior",
                    titles=["Engineer"],
                    domains=[],
                    core_skills=[],
                )
                session.add(
                    Cv(
                        name=f"CV {n}",
                        filename=f"{n}.pdf",
                        pdf=b"%PDF",
                        text=text,
                        summary=summary.model_dump(),
                    )
                )
            return project_id

    async def test_the_letter_is_written_from_the_picked_cv_and_the_pick_is_kept(
        self, harness: Harness, strategy: SeekerStrategy
    ) -> None:
        project_id = await self.searched_job(harness, strategy)
        with harness.db.session() as session:
            job_id = session.scalars(select(Item.id)).one()
            data_cv, backend_cv = session.scalars(select(Cv.id).order_by(Cv.id)).all()
        ratings = CvRatings(
            listing_language="en",
            ratings=[
                CvRating(cv_id=data_cv, skills=1, title=1, seniority=4, domain=2, reason="data"),
                CvRating(cv_id=backend_cv, skills=4, title=4, seniority=4, domain=2, reason="api"),
            ],
            concerns=[],
        )
        harness.script({"cv-picker": [ratings], **letter("Dear Hiring Team,", BODY)})
        harness.script(letter("Dear Hiring Team,", BODY))

        for _ in range(2):
            run_id = harness.manager.start(project_id, "seeker.letters", {"item_id": job_id})
            assert (await finish(harness, run_id)).status == RunStatus.COMPLETE

        assert len(harness.runner.payloads["cv-picker"]) == 1, "the second letter reuses the pick"
        for sent in harness.runner.payloads["expert-copywriter"]:
            assert isinstance(sent, LetterBrief)
            assert sent.sender_background == self.TEXTS[1]
        with harness.db.session() as session:
            job = session.get(Item, job_id)
            assert job is not None
            choice = CvChoice.model_validate(job.details[CV_CHOICE])
        assert (choice.cv_id, choice.picked_by) == (backend_cv, "ratings")
        assert [r.cv_id for r in choice.ranked] == [backend_cv, data_cv]
