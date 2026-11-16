from pathlib import Path

from sqlalchemy import select

from mozg_outreach.agents import AgentRunError
from mozg_outreach.domain import RunStatus
from mozg_outreach.pipelines.leads.enrichment import IDENTITY
from mozg_outreach.pipelines.seeker.models import JobContacts, SeekerStrategy
from mozg_outreach.runs.flow import GateAnswer
from mozg_outreach.runs.seeker import CONTACTS, JUDGMENT, LISTING
from mozg_outreach.storage.db import Database
from mozg_outreach.storage.models import Entity, Item, ItemLink, Run, SourceConfig
from mozg_outreach.storage.repositories import ItemRepository
from tests.pipelines.conftest import HangingRunner
from tests.pipelines.leads.conftest import company, identity, person

from .conftest import FEED_URL, Harness, judged


def run_row(harness: Harness, run_id: int) -> Run:
    with harness.db.session() as session:
        run = session.get(Run, run_id)
        assert run is not None
        return run


class TestSeekerSearch:
    async def test_gate_feedback_replans_then_approval_finishes(
        self, harness: Harness, strategy: SeekerStrategy
    ) -> None:
        project_id = harness.seeker_project()
        harness.script(
            {
                "seeker-strategist": [strategy, strategy],
                "seeker-judge": [judged()],
                "lead-identifier": [identity(company(), [person()])],
            }
        )
        run_id = harness.manager.start(project_id, "seeker.search", {})
        await harness.manager.wait(run_id)
        parked = run_row(harness, run_id)
        assert (parked.status, parked.phase) == (RunStatus.AWAITING_GATE, "approve_strategy")
        assert parked.pending_gate is not None
        assert parked.pending_gate["kind"] == "strategy"

        harness.manager.answer_gate(run_id, GateAnswer(approve=False, feedback="More Go roles"))
        await harness.manager.wait(run_id)
        replanned = harness.runner.payloads["seeker-strategist"][1].model_dump()
        assert replanned["feedback"] == "More Go roles"

        harness.manager.answer_gate(run_id, GateAnswer(approve=True))
        await harness.manager.wait(run_id)
        done = run_row(harness, run_id)
        assert done.status == RunStatus.COMPLETE
        assert done.cost_usd > 0
        with harness.db.session() as session:
            [item] = ItemRepository(session).report(project_id)
            assert item.dedup_key == FEED_URL
            assert {LISTING, JUDGMENT, IDENTITY} <= set(item.details)
            assert item.score_total is not None
            cursor = session.query(SourceConfig.cursor).scalar()
            linked = session.execute(
                select(ItemLink.role, Entity.name).join(Entity).where(ItemLink.item_id == item.id)
            ).all()
        assert set(linked) == {("subject", "Acme GmbH"), ("contact", "Jane Doe")}
        [lead] = harness.runner.payloads["lead-identifier"]
        assert lead.model_dump()["organization"] == "Acme"
        assert cursor == "2026-11-13T09:00:00+00:00"
        kinds = [e.kind for e in harness.manager.log.after(run_id, 0)]
        assert kinds.count("gate") == 2
        assert kinds[-1] == "status"

    async def test_revised_strategy_from_the_gate_is_used(
        self, harness: Harness, strategy: SeekerStrategy
    ) -> None:
        project_id = harness.seeker_project()
        harness.script(
            {
                "seeker-strategist": [strategy],
                "seeker-judge": [judged()],
                "lead-identifier": [identity()],
            }
        )
        run_id = harness.manager.start(project_id, "seeker.search", {})
        await harness.manager.wait(run_id)
        edited = strategy.model_copy(update={"must_have_skills": ["Python", "Go"]})
        harness.manager.answer_gate(
            run_id, GateAnswer(approve=True, revised=edited.model_dump(mode="json"))
        )
        await harness.manager.wait(run_id)
        assert run_row(harness, run_id).status == RunStatus.COMPLETE
        used = SeekerStrategy.model_validate(run_row(harness, run_id).state["strategy"])
        assert used.must_have_skills == ["Python", "Go"]


class TestLifecycle:
    async def test_failure_is_recorded_and_resume_retries_the_phase(
        self, harness: Harness, strategy: SeekerStrategy
    ) -> None:
        project_id = harness.seeker_project()
        crash = AgentRunError("seeker-strategist", "error_during_execution", "boom")
        harness.script({"seeker-strategist": [crash]})
        run_id = harness.manager.start(project_id, "seeker.search", {})
        await harness.manager.wait(run_id)
        failed = run_row(harness, run_id)
        assert failed.status == RunStatus.FAILED
        assert failed.error is not None
        assert "boom" in failed.error

        harness.script({"seeker-strategist": [strategy]})
        harness.manager.resume(run_id)
        await harness.manager.wait(run_id)
        assert run_row(harness, run_id).status == RunStatus.AWAITING_GATE

    async def test_cancel_stops_a_running_phase(self, db: Database, tmp_path: Path) -> None:
        runner = HangingRunner()
        harness = Harness(db, tmp_path, runner)
        run_id = harness.manager.start(harness.seeker_project(), "seeker.search", {})
        await runner.started.wait()
        harness.manager.cancel(run_id)
        await harness.manager.wait(run_id)
        assert run_row(harness, run_id).status == RunStatus.CANCELLED

    def test_restart_marks_unfinished_runs_failed(self, harness: Harness) -> None:
        project_id = harness.seeker_project()
        with harness.db.session() as session:
            run = Run(project_id=project_id, pipeline="seeker.search", status=RunStatus.RUNNING)
            session.add(run)
            session.flush()
            run_id = run.id
        harness.manager.recover()
        stale = run_row(harness, run_id)
        assert stale.status == RunStatus.FAILED
        assert stale.error == "interrupted: the server stopped mid-run"


async def test_a_lead_straight_from_the_search_can_be_enriched(
    harness: Harness, strategy: SeekerStrategy
) -> None:
    contacts = JobContacts(
        named_contact=None,
        careers_email="jobs@acme.example",
        careers_email_source="https://acme.example/careers",
        linkedin_company=None,
        pattern_guesses=[],
        apply_url=None,
        concerns=[],
    )
    project_id = harness.seeker_project()
    harness.script(
        {
            "seeker-strategist": [strategy],
            "seeker-judge": [judged()],
            "job-contact-finder": [contacts],
        }
    )
    search = harness.manager.start(project_id, "seeker.search", {})
    await harness.manager.wait(search)
    harness.manager.answer_gate(search, GateAnswer(approve=True))
    await harness.manager.wait(search)
    with harness.db.session() as session:
        item_id = session.scalars(select(Item)).one().id

    enrich = harness.manager.start(project_id, "seeker.enrich", {"item_id": item_id})
    await harness.manager.wait(enrich)
    done = run_row(harness, enrich)
    assert done.status == RunStatus.COMPLETE, done.error
    with harness.db.session() as session:
        enriched = session.get(Item, item_id)
        assert enriched is not None
        stored = JobContacts.model_validate(enriched.details[CONTACTS])
        assert stored.careers_email == "jobs@acme.example"
