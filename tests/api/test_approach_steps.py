from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from mozg_outreach.domain import ItemKind
from mozg_outreach.pipelines.biz.approach_models import (
    ApproachPlan,
    PlanContact,
    PlannedTouch,
    PlanRoute,
    StoredApproach,
)
from mozg_outreach.pipelines.biz.outreach import APPROACH
from mozg_outreach.storage.models import Draft, Item, JsonObject, MailThread
from tests.api.conftest import Api, open_api
from tests.pipelines.conftest import RecordedRunner

ANN = PlanContact(
    ref="c1",
    name="Ann Lee",
    title="COO",
    relevance="",
    routes=[
        PlanRoute(
            ref="c1.email", channel="email", address="ann@acme.example", confidence="published"
        ),
        PlanRoute(ref="c1.phone", channel="phone", address="+44 20 1234", confidence="published"),
    ],
)


def touch(step: int, day: int, route_ref: str) -> PlannedTouch:
    return PlannedTouch(
        step=step,
        day=day,
        contact_ref="c1",
        route_ref=route_ref,
        purpose="open" if step == 1 else "follow_up",
        angle="cost",
        hook_ref=None,
        ask="Worth a look?",
        talking_points="",
    )


class Steps:
    def __init__(self, api: Api) -> None:
        self.api = api

    async def company(self) -> int:
        """Acme with a three-touch plan to Ann: email, call, email."""
        project_id = await self.api.project("biz")
        approach = StoredApproach(
            plan=ApproachPlan(
                summary="",
                first_contact_ref="c1",
                rationale="",
                touches=[touch(1, 0, "c1.email"), touch(2, 3, "c1.phone"), touch(3, 7, "c1.email")],
                avoid=[],
                concerns=[],
            ),
            contacts=[ANN],
            hooks=[],
            planned_on="2026-11-16",
        )
        with self.api.services.db.session() as session:
            item = Item(
                project_id=project_id,
                kind=ItemKind.COMPANY,
                dedup_key="acme",
                title="Acme",
                details={APPROACH: approach.model_dump(mode="json")},
            )
            session.add(item)
            session.flush()
            return item.id

    async def done(self, item_id: int, step: int, on: str | None = None) -> JsonObject:
        return await self.api.ok(
            "POST", f"/api/items/{item_id}/approach/steps/{step}/done", {"on": on}
        )

    async def item(self, item_id: int) -> JsonObject:
        return await self.api.ok("GET", f"/api/items/{item_id}")


@pytest.fixture
async def steps(tmp_path: Path) -> AsyncIterator[Steps]:
    async for api in open_api(tmp_path, RecordedRunner({})):
        yield Steps(api)


def logged(item: JsonObject) -> list[tuple[object, object]]:
    interactions = item["interactions"]
    assert isinstance(interactions, list)
    return [(i["kind"], i["note"]) for i in interactions]


class TestMarkDone:
    async def test_the_list_shows_the_next_step_and_when_it_is_due(self, steps: Steps) -> None:
        item_id = await steps.company()
        before = await steps.item(item_id)
        assert before["approach_step"] == {
            "step": 1,
            "total": 3,
            "done": 0,
            "channel": "email",
            "reader": "Ann Lee",
            "due": None,
        }

        current = await steps.done(item_id, 1, "2026-11-16")

        assert (current["step"], current["channel"], current["due"]) == (2, "phone", "2026-11-19")
        project_id = before["project_id"]
        (row,) = await steps.api.ok_list(f"/api/projects/{project_id}/items")
        assert row["approach_step"] == current
        item = await steps.item(item_id)
        assert logged(item) == [("sent", "Step 1 (open): email to Ann Lee")]
        assert str(item["interactions"][0]["happened_at"]).startswith("2026-11-16")  # type: ignore[index]

    async def test_a_call_is_logged_as_called_and_undo_takes_it_back(self, steps: Steps) -> None:
        item_id = await steps.company()
        await steps.done(item_id, 2)

        assert logged(await steps.item(item_id)) == [
            ("called", "Step 2 (follow-up): call to Ann Lee")
        ]
        current = await steps.api.ok("DELETE", f"/api/items/{item_id}/approach/steps/2/done")

        assert (current["step"], current["done"]) == (1, 0)
        assert logged(await steps.item(item_id)) == []

    async def test_a_letter_gmail_saw_sent_is_not_logged_twice(self, steps: Steps) -> None:
        item_id = await steps.company()
        with steps.api.services.db.session() as session:
            draft = Draft(item_id=item_id, channel="email", recipient="Ann Lee", step=1, body="Hi")
            session.add(draft)
            session.flush()
            session.add(
                MailThread(
                    item_id=item_id,
                    recipient="Ann Lee",
                    step=1,
                    draft_id=draft.id,
                    address="ann@acme.example",
                    gmail_thread_id=9001,
                    sent_at=datetime(2026, 11, 16, 9, tzinfo=UTC),
                )
            )

        current = await steps.done(item_id, 1)

        assert current["step"] == 2
        assert logged(await steps.item(item_id)) == []

    @pytest.mark.parametrize(
        ("step", "status", "message"),
        [(1, 409, "step 1 is already done"), (9, 404, "no step 9")],
    )
    async def test_refusals(self, steps: Steps, step: int, status: int, message: str) -> None:
        item_id = await steps.company()
        await steps.done(item_id, 1, date(2026, 11, 16).isoformat())

        url = f"/api/items/{item_id}/approach/steps/{step}/done"
        refused = await steps.api.client.post(url, json={})

        assert refused.status_code == status
        assert message in refused.text
