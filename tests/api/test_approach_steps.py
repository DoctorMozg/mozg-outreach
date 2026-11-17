from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
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

    async def state(self, item_id: int) -> JsonObject:
        current = (await self.item(item_id))["approach_step"]
        assert isinstance(current, dict)
        return current


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
            "state": "active",
            "reason": None,
            "resume_on": None,
            "recycle_on": None,
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


class TestSequenceState:
    async def log(
        self, steps: Steps, item_id: int, kind: str, on: date, until: date | None = None
    ) -> None:
        body = {
            "kind": kind,
            "happened_at": f"{on.isoformat()}T10:00:00",
            "until": until and until.isoformat(),
        }
        await steps.api.ok("POST", f"/api/items/{item_id}/interactions", body)

    async def started(self, steps: Steps, on: date) -> int:
        item_id = await steps.company()
        await steps.done(item_id, 1, on.isoformat())
        return item_id

    @pytest.mark.parametrize(
        ("kind", "reason"),
        [
            ("replied", "replied"),
            ("refused", "refused"),
            ("opted_out", "asked not to be contacted"),
            ("bounced", "bounced"),
        ],
    )
    async def test_an_outcome_after_the_first_touch_stops_it(
        self, steps: Steps, kind: str, reason: str
    ) -> None:
        item_id = await self.started(steps, date(2026, 11, 16))
        await self.log(steps, item_id, kind, date(2026, 11, 18))

        current = await steps.state(item_id)

        assert current["state"] == "stopped"
        assert current["reason"] == f"{reason} on 2026-11-18"
        assert (current["step"], current["due"], current["recycle_on"]) == (None, None, None)

    async def test_an_outcome_before_the_sequence_started_does_not_stop_it(
        self, steps: Steps
    ) -> None:
        item_id = await steps.company()
        await self.log(steps, item_id, "replied", date(2026, 11, 1))
        await steps.done(item_id, 1, "2026-11-16")

        current = await steps.state(item_id)

        assert (current["state"], current["step"]) == ("active", 2)

    async def test_not_now_comes_back_on_their_date_or_after_ninety_days(
        self, steps: Steps
    ) -> None:
        dated = await self.started(steps, date(2026, 11, 16))
        undated = await self.started(steps, date(2026, 11, 16))
        await self.log(steps, dated, "not_now", date(2026, 11, 18), until=date(2027, 2, 1))
        await self.log(steps, undated, "not_now", date(2026, 11, 18))

        assert (await steps.state(dated))["recycle_on"] == "2027-02-01"
        assert (await steps.state(undated))["recycle_on"] == "2027-02-16"

    async def test_a_finished_silent_sequence_is_re_planned_ninety_days_after_its_last_touch(
        self, steps: Steps
    ) -> None:
        item_id = await self.started(steps, date(2026, 11, 16))
        await steps.done(item_id, 2, "2026-11-19")
        await steps.done(item_id, 3, "2026-11-23")

        current = await steps.state(item_id)

        assert (current["state"], current["recycle_on"]) == ("done", "2027-02-21")

    async def test_out_of_office_pauses_until_two_working_days_after_the_return(
        self, steps: Steps
    ) -> None:
        today = date.today()
        item_id = await self.started(steps, today - timedelta(days=1))
        back = today + timedelta(days=(4 - today.weekday()) % 7 + 7)  # a Friday next week
        await self.log(steps, item_id, "out_of_office", today, until=back)

        current = await steps.state(item_id)

        resume = (back + timedelta(days=4)).isoformat()  # Friday + 2 working days = Tuesday
        assert (current["state"], current["step"]) == ("paused", 2)
        assert (current["resume_on"], current["due"]) == (resume, resume)
        assert current["reason"] == f"out of office (back {back.isoformat()})"

    async def test_a_past_return_date_no_longer_pauses(self, steps: Steps) -> None:
        item_id = await self.started(steps, date(2026, 11, 2))
        await self.log(steps, item_id, "out_of_office", date(2026, 11, 3), until=date(2026, 11, 6))

        current = await steps.state(item_id)

        assert (current["state"], current["due"]) == ("active", "2026-11-05")


class TestReplanNote:
    @pytest.mark.parametrize(
        ("body", "feedback"),
        [
            (
                {"feedback": "  Lead with the COO, skip LinkedIn.  "},
                "Lead with the COO, skip LinkedIn.",
            ),
            ({"feedback": "   "}, None),
            (None, None),
        ],
        ids=["note", "blank-note", "no-body"],
    )
    async def test_the_note_goes_to_the_re_plan_run(
        self, steps: Steps, body: JsonObject | None, feedback: str | None
    ) -> None:
        item_id = await steps.company()

        run = await steps.api.ok("POST", f"/api/items/{item_id}/approach", body)

        state = run["state"]
        assert isinstance(state, dict)
        assert state.get("feedback") == feedback
