from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from mozg_outreach.apply.models import ApplicantProfile
from mozg_outreach.domain import ItemKind
from mozg_outreach.pipelines.seeker.models import SeekerBrief
from mozg_outreach.storage.models import Item
from tests.pipelines.conftest import RecordedRunner
from tests.pipelines.seeker.conftest import CV_TEXT, listing
from tests.runs.conftest import FakeDesk

from .conftest import Api, open_api

APPLY_URL = "https://job-boards.greenhouse.io/acme/jobs/1"


@pytest.fixture
def desk() -> FakeDesk:
    return FakeDesk()


@pytest.fixture
async def api(tmp_path: Path, desk: FakeDesk) -> AsyncIterator[Api]:
    async for opened in open_api(tmp_path, RecordedRunner({}), applications=desk):
        yield opened


async def a_job(api: Api) -> int:
    brief = SeekerBrief(
        cv_text=CV_TEXT,
        preferences="Remote EU",
        applicant=ApplicantProfile(first_name="Stan", last_name="Petrov", email="stan@example.com"),
    )
    project_id = await api.project("seeker", brief.model_dump(mode="json"))
    with api.services.db.session() as session:
        item = Item(
            project_id=project_id,
            kind=ItemKind.JOB,
            dedup_key="https://jobs.acme.example/backend",
            title="Senior Backend Engineer",
            details={"listing": listing(apply_url=APPLY_URL).model_dump(mode="json")},
        )
        session.add(item)
        session.flush()
        return item.id


async def test_filling_again_cancels_the_open_application_and_starts_over(
    api: Api, desk: FakeDesk
) -> None:
    item_id = await a_job(api)
    first = await api.ok("POST", f"/api/items/{item_id}/apply")
    assert (await api.settle(int(str(first["id"]))))["status"] == "awaiting_gate"

    second = await api.ok("POST", f"/api/items/{item_id}/apply")
    settled = await api.settle(int(str(second["id"])))

    assert second["id"] != first["id"]
    assert (await api.ok("GET", f"/api/runs/{first['id']}"))["status"] == "cancelled"
    assert settled["status"] == "awaiting_gate"
    assert [prepared[0] for prepared in desk.prepared] == [item_id, item_id]
