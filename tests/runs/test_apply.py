import pytest
from sqlalchemy import select

from mozg_outreach.apply.models import ApplicantProfile
from mozg_outreach.domain import ItemKind, RunStatus
from mozg_outreach.runs.flow import GateAnswer
from mozg_outreach.runs.manager import RunStateError
from mozg_outreach.storage.models import Draft, Interaction, Item, Run
from tests.pipelines.seeker.conftest import listing

from .conftest import Harness
from .test_biz_items import concerns
from .test_seeker import finish

APPLY_URL = "https://job-boards.greenhouse.io/acme/jobs/1"
APPLICANT = ApplicantProfile(first_name="Stan", last_name="Petrov", email="stan@example.com")


def job(harness: Harness, project_id: int) -> int:
    with harness.db.session() as session:
        item = Item(
            project_id=project_id,
            kind=ItemKind.JOB,
            dedup_key="https://jobs.acme.example/backend",
            title="Senior Backend Engineer",
            details={"listing": listing(apply_url=APPLY_URL).model_dump(mode="json")},
            selected=True,
        )
        session.add(item)
        session.flush()
        session.add(Draft(item_id=item.id, channel="cover_letter", body="Dear Acme, ..."))
        return item.id


async def at_the_gate(harness: Harness) -> tuple[int, int]:
    project_id = harness.seeker_project(applicant=APPLICANT)
    item_id = job(harness, project_id)
    run_id = harness.manager.start(project_id, "seeker.apply", {"item_id": item_id})
    run = await finish(harness, run_id)
    assert run.status == RunStatus.AWAITING_GATE, run.error
    return run_id, item_id


def interactions(harness: Harness) -> list[tuple[str, str]]:
    with harness.db.session() as session:
        return [(i.kind, i.note) for i in session.scalars(select(Interaction))]


class TestApplyFlow:
    async def test_the_form_is_filled_from_the_brief_cv_and_letter(self, harness: Harness) -> None:
        run_id, item_id = await at_the_gate(harness)

        [(prepared_item, url, application)] = harness.desk.prepared
        assert (prepared_item, url) == (item_id, APPLY_URL)
        assert application.profile == APPLICANT
        assert application.letter == "Dear Acme, ..."
        assert application.cv_pdf is None, "an empty CV library leaves the resume to the user"
        with harness.db.session() as session:
            run = session.get_one(Run, run_id)
            assert run.pending_gate is not None
            assert run.pending_gate["kind"] == "application"
            assert run.state["concerns"] == ["Check the notice period."]

    async def test_a_confirmation_page_marks_the_lead_sent(self, harness: Harness) -> None:
        run_id, item_id = await at_the_gate(harness)
        assert harness.desk.on_sent is not None

        harness.desk.on_sent("https://job-boards.greenhouse.io/acme/jobs/1/confirmation")
        done = await finish(harness, run_id)

        assert done.status == RunStatus.COMPLETE, done.error
        assert interactions(harness) == [
            ("sent", "Confirmation page: https://job-boards.greenhouse.io/acme/jobs/1/confirmation")
        ]
        assert harness.desk.closed == [item_id]

    async def test_a_late_confirmation_after_the_users_answer_is_ignored(
        self, harness: Harness
    ) -> None:
        run_id, _ = await at_the_gate(harness)
        harness.manager.answer_gate(run_id, GateAnswer(approve=True))
        await finish(harness, run_id)
        assert harness.desk.on_sent is not None

        harness.desk.on_sent("https://example.com/thanks")

        assert interactions(harness) == [("sent", "Submitted by hand in the application browser.")]

    async def test_not_sending_logs_nothing(self, harness: Harness) -> None:
        run_id, _ = await at_the_gate(harness)

        harness.manager.answer_gate(run_id, GateAnswer(approve=False, feedback="Role closed."))
        done = await finish(harness, run_id)

        assert done.status == RunStatus.COMPLETE
        assert interactions(harness) == []
        assert "Role closed." in concerns(done)


async def test_apply_needs_applicant_details(harness: Harness) -> None:
    project_id = harness.seeker_project()
    item_id = job(harness, project_id)
    with pytest.raises(RunStateError, match="applicant details"):
        harness.manager.start(project_id, "seeker.apply", {"item_id": item_id})
