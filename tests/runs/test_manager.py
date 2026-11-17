from pathlib import Path

import pytest
from sqlalchemy import select

from mozg_outreach.agents import AgentRunError
from mozg_outreach.domain import RunStatus
from mozg_outreach.pipelines.leads.enrichment import IDENTITY
from mozg_outreach.pipelines.seeker.models import JobContacts, SeekerStrategy
from mozg_outreach.runs.flow import GateAnswer
from mozg_outreach.runs.seeker import CONTACTS, JUDGMENT, LISTING
from mozg_outreach.settings.store import SettingsStore, UserSettings
from mozg_outreach.storage.db import Database
from mozg_outreach.storage.models import (
    Draft,
    Entity,
    Item,
    ItemLink,
    Project,
    Run,
    SourceConfig,
)
from mozg_outreach.storage.repositories import ItemRepository
from tests.pipelines.conftest import HangingRunner, Scripted
from tests.pipelines.leads.conftest import company, identity, person
from tests.pipelines.seeker.test_letters import BODY
from tests.runs.test_seeker import letter

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


CAREERS = JobContacts(
    named_contact=None,
    careers_email="jobs@acme.example",
    careers_email_source="https://acme.example/careers",
    linkedin_company=None,
    pattern_guesses=[],
    apply_url=None,
    concerns=[],
)


async def search_jobs(harness: Harness, project_id: int) -> Run:
    """Runs a job search, approving the strategy when it asks; a later search reuses it."""
    run_id = harness.manager.start(project_id, "seeker.search", {})
    await harness.manager.wait(run_id)
    if run_row(harness, run_id).status == RunStatus.AWAITING_GATE:
        harness.manager.answer_gate(run_id, GateAnswer(approve=True))
        await harness.manager.wait(run_id)
    return run_row(harness, run_id)


def job_script(
    strategy: SeekerStrategy, letters: dict[str, list[Scripted]]
) -> dict[str, list[Scripted]]:
    """One search that finds and identifies one job, and what enriching it needs."""
    return {
        "seeker-strategist": [strategy],
        "seeker-judge": [judged()],
        "lead-identifier": [identity()],
        "job-contact-finder": [CAREERS],
        **letters,
    }


async def enrich_found_job(
    harness: Harness,
    strategy: SeekerStrategy,
    letters: dict[str, list[Scripted]],
    sender: str | None,
) -> tuple[Run, int]:
    """Finds one job with a search, then enriches it; returns the enrich run and the item."""
    project_id = harness.seeker_project()
    with harness.db.session() as session:
        session.get_one(Project, project_id).sender_name = sender
    harness.script(job_script(strategy, letters))
    search = await search_jobs(harness, project_id)
    assert search.status == RunStatus.COMPLETE, search.error
    with harness.db.session() as session:
        item_id = session.scalars(select(Item)).one().id
    enrich = harness.manager.start(project_id, "seeker.enrich", {"item_id": item_id})
    await harness.manager.wait(enrich)
    return run_row(harness, enrich), item_id


class TestJobEnrichment:
    async def test_finds_contacts_and_writes_the_cover_letter(
        self, harness: Harness, strategy: SeekerStrategy
    ) -> None:
        script = letter("Dear Hiring Team,", BODY)
        done, item_id = await enrich_found_job(harness, strategy, script, "Stan Petrov")

        assert done.status == RunStatus.COMPLETE, done.error
        with harness.db.session() as session:
            enriched = session.get_one(Item, item_id)
            stored = JobContacts.model_validate(enriched.details[CONTACTS])
            assert stored.careers_email == "jobs@acme.example"
            [draft] = session.scalars(select(Draft)).all()
            assert (draft.channel, draft.review["address"]) == ("cover_letter", "jobs@acme.example")
            assert draft.subject is not None
            assert draft.subject.startswith("Application: ")

    async def test_without_a_sender_name_it_keeps_the_contacts_and_says_why(
        self, harness: Harness, strategy: SeekerStrategy
    ) -> None:
        done, item_id = await enrich_found_job(harness, strategy, {}, None)

        assert done.status == RunStatus.COMPLETE, done.error
        concerns = done.state["concerns"]
        assert isinstance(concerns, list)
        assert any("set the sender name" in c for c in concerns)
        with harness.db.session() as session:
            assert CONTACTS in session.get_one(Item, item_id).details
            assert session.scalars(select(Draft)).all() == []


def set_minimum(harness: Harness, minimum: int | None) -> None:
    with harness.db.session() as session:
        SettingsStore(session).save(UserSettings(auto_enrich_min_score=minimum))


async def finished_enrichments(harness: Harness) -> list[Run]:
    with harness.db.session() as session:
        ids = list(session.scalars(select(Run.id).where(Run.pipeline == "seeker.enrich")))
    for run_id in ids:
        await harness.manager.wait(run_id)
    return [run_row(harness, run_id) for run_id in ids]


class TestAutoEnrichment:
    """The scripted job scores 83.5."""

    async def test_a_search_enriches_a_lead_at_the_minimum(
        self, harness: Harness, strategy: SeekerStrategy
    ) -> None:
        project_id = harness.seeker_project()
        set_minimum(harness, 80)
        harness.script(job_script(strategy, letter("Dear Hiring Team,", BODY)))

        search = await search_jobs(harness, project_id)

        assert search.status == RunStatus.COMPLETE, search.error
        [enrich] = await finished_enrichments(harness)
        assert enrich.status == RunStatus.COMPLETE, enrich.error
        with harness.db.session() as session:
            item = session.scalars(select(Item)).one()
            assert enrich.state["item_id"] == item.id
            assert item.enriched
            [draft] = session.scalars(select(Draft)).all()
            assert draft.channel == "cover_letter"

    @pytest.mark.parametrize("minimum", [84, None], ids=["below the minimum", "switched off"])
    async def test_otherwise_the_search_starts_nothing(
        self, harness: Harness, strategy: SeekerStrategy, minimum: int | None
    ) -> None:
        project_id = harness.seeker_project()
        set_minimum(harness, minimum)
        harness.script(job_script(strategy, {}))

        search = await search_jobs(harness, project_id)

        assert search.status == RunStatus.COMPLETE, search.error
        assert await finished_enrichments(harness) == []

    async def test_a_lead_found_again_after_enrichment_is_not_enriched_twice(
        self, harness: Harness, strategy: SeekerStrategy
    ) -> None:
        project_id = harness.seeker_project()
        set_minimum(harness, 80)
        harness.script(job_script(strategy, letter("Dear Hiring Team,", BODY)))
        await search_jobs(harness, project_id)
        await finished_enrichments(harness)
        harness.script(job_script(strategy, {}))

        again = await search_jobs(harness, project_id)

        assert again.status == RunStatus.COMPLETE, again.error
        assert again.state["item_ids"], "the second search scored the lead again"
        assert len(await finished_enrichments(harness)) == 1
