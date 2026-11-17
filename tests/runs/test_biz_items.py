import pytest

from mozg_outreach.agents import AgentRunError
from mozg_outreach.domain import ProjectMode, RunStatus
from mozg_outreach.letters.models import DraftReview, LetterBrief
from mozg_outreach.pipelines.biz.discovery import dedup_companies
from mozg_outreach.pipelines.biz.letters import CANDIDATES
from mozg_outreach.pipelines.biz.models import ScoutResult, Strategy
from mozg_outreach.pipelines.biz.outreach import APPROACH, DOSSIER
from mozg_outreach.storage.models import Draft, Item, Project, Run
from mozg_outreach.storage.repositories import ItemRepository
from tests.pipelines.biz.conftest import load_fixture, outreach_script

from .conftest import Harness
from .test_seeker import finish


@pytest.fixture
def katana(harness: Harness) -> int:
    """A company from a finished biz search whose state holds the strategy."""
    strategy = load_fixture("strategy", Strategy)
    with harness.db.session() as session:
        project = Project(name="Biz", mode=ProjectMode.BIZ, sender_name="Stan")
        session.add(project)
        session.flush()
        search = Run(
            project_id=project.id,
            pipeline="biz.search",
            state={"strategy": strategy.model_dump(mode="json")},
        )
        session.add(search)
        session.flush()
        company = dedup_companies(load_fixture("scout_results", list[ScoutResult])[0].companies)[0]
        item = ItemRepository(session).upsert(project.id, search.id, company)
        session.flush()
        return item.id


async def enrich(harness: Harness, item_id: int) -> tuple[Run, Item, list[Draft]]:
    with harness.db.session() as session:
        project_id = session.get_one(Item, item_id).project_id
    run = await finish(
        harness, harness.manager.start(project_id, "biz.enrich", {"item_id": item_id})
    )
    with harness.db.session() as session:
        item = session.get_one(Item, item_id)
        drafts = list(session.query(Draft).filter_by(item_id=item_id))
        session.expunge_all()
    return run, item, drafts


def concerns(run: Run) -> list[str]:
    found = run.state["concerns"]
    assert isinstance(found, list)
    return [str(c) for c in found]


class TestEnrichFlow:
    async def test_enrichment_plans_the_approach_and_drafts_the_letters(
        self, harness: Harness, katana: int
    ) -> None:
        harness.script(outreach_script(CANDIDATES))

        run, item, drafts = await enrich(harness, katana)

        assert run.status == RunStatus.COMPLETE, run.error
        assert {DOSSIER, APPROACH} <= item.details.keys()
        assert [(d.recipient, d.channel, d.step) for d in drafts] == [
            ("Jane Example", "email", 1),
            ("Jane Example", "email", 2),
            ("Jane Example", "email", 3),
        ]
        briefs = harness.runner.payloads["expert-copywriter"]
        assert all(isinstance(b, LetterBrief) and b.ask and b.touch for b in briefs)
        review = DraftReview.model_validate(drafts[0].review)
        assert [(c.score, c.chosen) for c in review.candidates] == [
            (48, True),
            (48, False),
            (48, False),
        ]

    async def test_a_failed_plan_still_drafts_with_the_default_routing(
        self, harness: Harness, katana: int
    ) -> None:
        script = outreach_script(CANDIDATES)
        script["outreach-approach-planner"] = [
            AgentRunError("outreach-approach-planner", "error_max_turns", "ran out of turns")
        ]
        harness.script(script)

        run, item, drafts = await enrich(harness, katana)

        assert run.status == RunStatus.COMPLETE, run.error
        assert APPROACH not in item.details
        assert any("No approach plan" in c for c in concerns(run))
        assert [d.recipient for d in drafts] == ["Jane Example"]

    async def test_without_a_sender_the_dossier_and_plan_stay_and_letters_wait(
        self, harness: Harness, katana: int
    ) -> None:
        with harness.db.session() as session:
            item = session.get_one(Item, katana)
            session.get_one(Project, item.project_id).sender_name = None
        harness.script(outreach_script())

        run, item, drafts = await enrich(harness, katana)

        assert run.status == RunStatus.COMPLETE, run.error
        assert {DOSSIER, APPROACH} <= item.details.keys()
        assert drafts == []
        assert any("set the sender name" in c for c in concerns(run))
