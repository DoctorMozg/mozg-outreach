from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from mozg_outreach.domain import ItemKind
from mozg_outreach.pipelines.biz.approach_models import (
    ApproachPlan,
    PlannedTouch,
    StoredApproach,
)
from mozg_outreach.pipelines.biz.outcomes import Rate, project_outcomes
from mozg_outreach.pipelines.biz.outreach import APPROACH
from mozg_outreach.storage.models import Draft, Item, MailThread, Project


class TestRate:
    def test_no_letters_is_the_flat_prior(self) -> None:
        assert Rate.of(0, 0) == Rate(sent=0, replied=0, mean=0.5, low=0.05, high=0.95)

    def test_matches_the_beta_quantiles(self) -> None:
        # Beta(2, 10): scipy's beta.ppf gives 0.0329 and 0.3639.
        rate = Rate.of(1, 10)
        assert (rate.mean, rate.low, rate.high) == (0.167, 0.033, 0.364)

    def test_more_letters_narrow_the_interval(self) -> None:
        few, many = Rate.of(1, 10), Rate.of(10, 100)
        assert many.high - many.low < few.high - few.low

    def test_more_replies_than_letters_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="replied 3 must be between 0 and sent 2"):
            Rate.of(3, 2)


def touch(step: int, angle: str, purpose: str) -> PlannedTouch:
    return PlannedTouch.model_validate(
        {
            "step": step,
            "day": 0 if step == 1 else 3,
            "contact_ref": "c1",
            "route_ref": "c1.email",
            "purpose": purpose,
            "angle": angle,
            "hook_ref": None,
            "ask": "Worth a look?",
            "talking_points": "",
        }
    )


def at(day: int) -> datetime:
    return datetime(2026, 11, day, 9, tzinfo=UTC)


class Letters:
    def __init__(self, session: Session, project: Project) -> None:
        self.session, self.project = session, project
        self.thread_ids = iter(range(5000, 6000))

    def item(self, key: str, plan: list[PlannedTouch] | None = None) -> Item:
        details = {}
        if plan:
            approach = StoredApproach(
                plan=ApproachPlan(
                    summary="",
                    first_contact_ref="c1",
                    rationale="",
                    touches=plan,
                    avoid=[],
                    concerns=[],
                ),
                contacts=[],
                hooks=[],
                planned_on="2026-11-01",
            )
            details = {APPROACH: approach.model_dump(mode="json")}
        row = Item(
            project_id=self.project.id,
            kind=ItemKind.COMPANY,
            dedup_key=key,
            title=key,
            details=details,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def letter(
        self,
        item: Item,
        step: int | None,
        sent: datetime | None,
        *,
        thread: int | None = None,
        replied: datetime | None = None,
        angle: str = "cost",
    ) -> MailThread:
        draft = Draft(
            item_id=item.id,
            channel="email",
            recipient="Ann",
            step=step,
            body="Hi",
            review={"angle_used": angle},
        )
        self.session.add(draft)
        self.session.flush()
        row = MailThread(
            item_id=item.id,
            recipient="Ann",
            step=step,
            draft_id=draft.id,
            address="a@x.example",
            gmail_thread_id=thread or next(self.thread_ids),
            sent_at=sent,
            replied_at=replied,
        )
        self.session.add(row)
        self.session.flush()
        return row


def test_counts_sent_letters_and_credits_a_reply_to_the_newest_before_it(
    session: Session, project: Project
) -> None:
    letters = Letters(session, project)
    planned = letters.item("acme", [touch(1, "cost", "open"), touch(2, "speed", "follow_up")])
    # Both steps share the thread, so the poller marks both replied; step 2 drew the reply.
    letters.letter(planned, 1, at(1), thread=77, replied=at(5))
    letters.letter(planned, 2, at(4), thread=77, replied=at(5))
    letters.letter(letters.item("bolt"), None, at(2), angle="risk")
    letters.letter(letters.item("cora"), None, None, angle="risk")

    outcomes = project_outcomes(session, project.id)

    assert outcomes.sent == 3
    assert [(r.group, r.name, r.rate.sent, r.rate.replied) for r in outcomes.rows] == [
        ("angle", "cost", 1, 0),
        ("angle", "risk", 1, 0),
        ("angle", "speed", 1, 1),
        ("purpose", "follow_up", 1, 1),
        ("purpose", "open", 2, 0),
    ]
