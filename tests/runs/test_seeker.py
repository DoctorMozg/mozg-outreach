from sqlalchemy import select

from mozg_outreach.domain import BriefFact, FieldValue, ItemKind, RunStatus
from mozg_outreach.letters.models import LetterBrief, LetterDraft
from mozg_outreach.naturalize.models import NaturalizeResult
from mozg_outreach.pipelines.seeker.models import (
    BoardPlan,
    JudgeResult,
    ScoutedListing,
    ScoutResult,
    SeekerStrategy,
)
from mozg_outreach.runs.flow import GateAnswer
from mozg_outreach.runs.seeker import WEB_SEARCH
from mozg_outreach.sources.models import SourceItem
from mozg_outreach.storage.models import Draft, Item, Run, SourceConfig
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
