from datetime import date
from pathlib import Path

import pytest

from mozg_outreach.agents import PromptLibrary
from mozg_outreach.letters.models import Hook
from mozg_outreach.pipelines.biz.approach import (
    ApproachOptions,
    ApproachPlanner,
    ApproachRequest,
    ApproachValidator,
    planned_sequences,
)
from mozg_outreach.pipelines.biz.approach_models import (
    ApproachPlan,
    DoneTouch,
    PlanContact,
    PlanHook,
    PlannedTouch,
    PlanRoute,
    RouteConfidence,
    StoredApproach,
    TouchChannel,
)
from mozg_outreach.pipelines.biz.enrichment_models import CompanyDossier
from mozg_outreach.pipelines.biz.models import Strategy
from mozg_outreach.pipelines.steps import PipelineSteps
from tests.pipelines.conftest import REPO_PROMPTS, RecordedRunner

ANGLE = "cost"


def route(ref: str, channel: TouchChannel, confidence: RouteConfidence = "published") -> PlanRoute:
    return PlanRoute(ref=ref, channel=channel, address=f"{ref}@x.example", confidence=confidence)


def contact(ref: str, *routes: PlanRoute) -> PlanContact:
    return PlanContact(
        ref=ref, name=ref.upper(), title="Partner", relevance="", routes=list(routes)
    )


CONTACTS = [
    contact("c1", route("c1.email", "email"), route("c1.linkedin", "linkedin", "linkedin")),
    contact("c2", route("c2.email", "email", "guessed")),
    contact("c3", route("c3.linkedin", "linkedin", "linkedin")),
    contact("c4", route("c4.linkedin", "linkedin", "linkedin")),
    contact(
        "inbox", route("inbox.email1", "email", "shared_inbox"), route("inbox.phone1", "phone")
    ),
]
HOOKS = [PlanHook(ref="h1", hook=Hook(signal="Opened a Dubai office", source="news"))]


def touch(step: int, day: int, contact_ref: str, route_ref: str, **extra: str) -> PlannedTouch:
    fields = {"angle": ANGLE, "hook_ref": "h1", **extra}
    return PlannedTouch(
        step=step,
        day=day,
        contact_ref=contact_ref,
        route_ref=route_ref,
        purpose="open" if step == 1 else "follow_up",
        ask="Worth a look?",
        talking_points="",
        **fields,
    )


def plan(*touches: PlannedTouch, first: str = "c1") -> ApproachPlan:
    return ApproachPlan(
        summary="",
        first_contact_ref=first,
        rationale="",
        touches=list(touches),
        avoid=[],
        concerns=[],
    )


def validate(
    *touches: PlannedTouch, contacts: list[PlanContact] = CONTACTS, first: str = "c1"
) -> ApproachPlan:
    return ApproachValidator(contacts, HOOKS, [ANGLE]).validate(plan(*touches, first=first))


class TestValidator:
    def test_a_good_sequence_is_kept_and_renumbered(self) -> None:
        kept = validate(touch(5, 0, "c1", "c1.email"), touch(9, 3, "c1", "c1.linkedin"))
        assert [(t.step, t.route_ref) for t in kept.touches] == [
            (1, "c1.email"),
            (2, "c1.linkedin"),
        ]
        assert kept.concerns == []

    @pytest.mark.parametrize(
        ("bad", "reason"),
        [
            (touch(2, 4, "c9", "c1.email"), "unknown contact c9"),
            (touch(2, 4, "c3", "c1.email"), "not one of c3's routes"),
            (touch(2, 4, "c1", "c1.linkedin", angle="speed"), "unknown angle"),
            (touch(2, 4, "c1", "c1.linkedin", hook_ref="h7"), "unknown hook h7"),
            (touch(2, 31, "c1", "c1.linkedin"), "past the 30-day sequence"),
            (touch(2, 1, "c1", "c1.linkedin"), "less than 2 days"),
            (touch(2, 0, "c3", "c3.linkedin"), "same company is touched that day"),
        ],
    )
    def test_a_broken_touch_is_dropped_with_a_reason(self, bad: PlannedTouch, reason: str) -> None:
        kept = validate(touch(1, 0, "c1", "c1.email"), bad)
        assert [t.route_ref for t in kept.touches] == ["c1.email"]
        assert reason in kept.concerns[0]

    def test_a_fourth_person_is_dropped(self) -> None:
        kept = validate(
            touch(1, 0, "c1", "c1.email"),
            touch(2, 2, "c3", "c3.linkedin"),
            touch(3, 4, "c2", "c2.email"),
            touch(4, 6, "c4", "c4.linkedin"),
        )
        assert [t.contact_ref for t in kept.touches] == ["c1", "c3", "c2"]
        assert "more than 3 people" in kept.concerns[0]

    def test_a_call_cannot_open_the_sequence(self) -> None:
        kept = validate(touch(1, 0, "inbox", "inbox.phone1"), touch(2, 2, "c1", "c1.email"))
        assert [t.route_ref for t in kept.touches] == ["c1.email"]
        assert "first touch must be an email or a LinkedIn message" in kept.concerns[0]

    @pytest.mark.parametrize(
        ("number", "flagged"), [("+971 4 123 4567", True), ("+44 20 7946 0000", False)]
    )
    def test_calls_to_uae_numbers_carry_the_tdra_rules(self, number: str, flagged: bool) -> None:
        phone = PlanRoute(ref="c1.phone", channel="phone", address=number, confidence="published")
        contacts = [contact("c1", route("c1.email", "email"), phone)]
        kept = validate(
            touch(1, 0, "c1", "c1.email"), touch(2, 3, "c1", "c1.phone"), contacts=contacts
        )
        assert len(kept.touches) == 2
        assert any("TDRA" in c for c in kept.concerns) is flagged

    def test_a_guessed_address_cannot_open_while_a_published_route_exists(self) -> None:
        kept = validate(touch(1, 0, "c2", "c2.email"), touch(2, 2, "c1", "c1.email"), first="c2")
        assert [t.contact_ref for t in kept.touches] == ["c1"]
        assert kept.first_contact_ref == "c1"
        assert "first kept touch goes to c1, not c2" in kept.concerns[-1]

    def test_a_guessed_address_may_open_when_nothing_else_exists(self) -> None:
        only_guess = [contact("c2", route("c2.email", "email", "guessed"))]
        kept = validate(touch(1, 0, "c2", "c2.email"), contacts=only_guess)
        assert len(kept.touches) == 1

    def test_only_one_guessed_address_per_company(self) -> None:
        guesses = [
            contact("c1", route("c1.email", "email")),
            contact("c2", route("c2.email", "email", "guessed")),
            contact("c3", route("c3.email", "email", "guessed")),
        ]
        kept = validate(
            touch(1, 0, "c1", "c1.email"),
            touch(2, 2, "c2", "c2.email"),
            touch(3, 4, "c3", "c3.email"),
            contacts=guesses,
        )
        assert [t.contact_ref for t in kept.touches] == ["c1", "c2"]
        assert "one guessed address" in kept.concerns[0]

    @pytest.mark.parametrize(
        ("contact_ref", "route_ref", "addressed_to"),
        [
            ("c1", "c1.email", "c3"),
            ("inbox", "inbox.email1", "inbox"),
            ("inbox", "inbox.email1", "c9"),
        ],
    )
    def test_addressed_to_names_a_person_behind_the_inbox(
        self, contact_ref: str, route_ref: str, addressed_to: str
    ) -> None:
        bad = touch(1, 0, contact_ref, route_ref, addressed_to=addressed_to)
        kept = validate(bad, touch(2, 3, "inbox", "inbox.email1", addressed_to="c1"))
        assert [t.addressed_to for t in kept.touches] == ["c1"]
        assert "needs an inbox touch and a known person" in kept.concerns[0]


class TestDoneTouches:
    DONE = (touch(1, 0, "c1", "c1.email"), touch(2, 3, "c1", "c1.linkedin"))

    def check(self, *touches: PlannedTouch, today: int = 5) -> ApproachPlan:
        validator = ApproachValidator(CONTACTS, HOOKS, [ANGLE], self.DONE, today)
        return validator.validate(plan(*touches))

    def test_done_touches_stay_and_new_ones_follow_them(self) -> None:
        kept = self.check(touch(1, 7, "c1", "c1.email"), touch(2, 10, "c3", "c3.linkedin"))

        assert [(t.step, t.day, t.route_ref) for t in kept.touches] == [
            (1, 0, "c1.email"),
            (2, 3, "c1.linkedin"),
            (3, 7, "c1.email"),
            (4, 10, "c3.linkedin"),
        ]
        assert kept.concerns == []

    @pytest.mark.parametrize(
        ("bad", "today", "reason"),
        [
            (touch(1, 4, "c3", "c3.linkedin"), 5, "day 4 is already past (today is day 5)"),
            (touch(1, 4, "c1", "c1.email"), 4, "less than 2 days after the previous touch"),
            (touch(1, 36, "c3", "c3.linkedin"), 5, "day 36 is past the 30-day sequence"),
        ],
    )
    def test_new_touches_are_checked_against_the_done_ones(
        self, bad: PlannedTouch, today: int, reason: str
    ) -> None:
        kept = self.check(bad, today=today)

        assert [t.step for t in kept.touches] == [1, 2]
        assert reason in kept.concerns[0]


class TestCurrentStep:
    def approach(self, *done: DoneTouch) -> StoredApproach:
        return StoredApproach(
            plan=plan(touch(1, 0, "c1", "c1.email"), touch(2, 4, "inbox", "inbox.phone1")),
            contacts=CONTACTS,
            hooks=HOOKS,
            planned_on="2026-11-16",
            done=list(done),
        )

    def test_before_the_first_touch_nothing_is_due(self) -> None:
        current = self.approach().current()
        assert (current.step, current.done, current.total) == (1, 0, 2)
        assert (current.channel, current.reader, current.due) == ("email", "C1", None)

    def test_later_touches_fall_due_from_the_first_ones_date(self) -> None:
        current = self.approach(DoneTouch(step=1, on=date(2026, 11, 18))).current()
        assert (current.step, current.channel, current.reader) == (2, "phone", "INBOX")
        assert current.due == date(2026, 11, 22)

    def test_a_finished_sequence_has_no_step(self) -> None:
        on = date(2026, 11, 18)
        current = self.approach(DoneTouch(step=1, on=on), DoneTouch(step=2, on=on)).current()
        assert (current.step, current.done, current.due) == (None, 2, None)


class TestPlannedSequences:
    def test_each_reader_gets_their_touches_in_order_and_emails_reply_in_thread(self) -> None:
        c1 = contact(
            "c1",
            route("c1.email", "email"),
            route("c1.phone", "phone"),
            route("c1.linkedin", "linkedin", "linkedin"),
        )
        contacts = [
            c1,
            contact("c2"),
            contact("inbox", route("inbox.email1", "email", "shared_inbox")),
        ]
        approach = StoredApproach(
            plan=plan(
                touch(1, 0, "c1", "c1.email"),
                touch(2, 2, "inbox", "inbox.email1", addressed_to="c2"),
                touch(3, 4, "c1", "c1.phone"),
                touch(4, 7, "c1", "c1.email"),
                touch(5, 9, "inbox", "inbox.email1", addressed_to="c2"),
                touch(6, 11, "c1", "c1.linkedin"),
            ),
            contacts=contacts,
            hooks=HOOKS,
            planned_on="2026-11-16",
        )

        first, second = planned_sequences(approach, "Acme")

        assert (first.contact.name, first.contact.linkedin) == ("C1", "c1.linkedin@x.example")
        assert [
            (t.touch.step, t.letter.channel if t.letter else None, t.in_thread)
            for t in first.touches
        ] == [(1, "email", False), (3, None, False), (4, "email", True), (6, "dm", False)]
        assert second.contact.name == "C2"
        assert [(t.touch.step, t.in_thread) for t in second.touches] == [(2, False), (5, True)]
        assert all(t.letter and t.letter.generic_inbox for t in second.touches)
        assert first.named
        assert second.named

    def test_an_inbox_touch_to_no_one_goes_to_the_company_team(self) -> None:
        inbox = contact("inbox", route("inbox.email1", "email", "shared_inbox"))
        approach = StoredApproach(
            plan=plan(touch(1, 0, "inbox", "inbox.email1")),
            contacts=[inbox.model_copy(update={"name": None})],
            hooks=HOOKS,
            planned_on="2026-11-16",
        )

        [team] = planned_sequences(approach, "Acme")

        assert (team.contact.name, team.named) == ("Acme team", False)


class TestOptions:
    def test_katana_offers_its_inbox_and_people_without_routes(
        self, dossier: CompanyDossier
    ) -> None:
        contacts = ApproachOptions("Katana", dossier).contacts()

        jane = contacts[0]
        assert (jane.ref, jane.name, jane.routes) == ("c1", "Jane Example", [])
        inbox = contacts[-1]
        assert inbox.ref == "inbox"
        assert [(r.address, r.confidence) for r in inbox.routes] == [
            ("hello@katanamrp.com", "shared_inbox")
        ], "a pattern-guessed shared address is not offered"

    def test_hooks_get_refs(self, dossier: CompanyDossier) -> None:
        hooks = ApproachOptions("Katana", dossier).hooks({})
        assert [h.ref for h in hooks[:2]] == ["h1", "h2"]


async def test_planner_sends_options_and_stores_the_validated_plan(
    tmp_path: Path, dossier: CompanyDossier, strategy: Strategy
) -> None:
    angle = strategy.outreach_angles[0].name
    raw = plan(
        touch(1, 0, "inbox", "inbox.email1", angle=angle),
        touch(2, 1, "inbox", "inbox.email1", angle=angle),
        first="inbox",
    )
    runner = RecordedRunner({"outreach-approach-planner": [raw]})
    planner = ApproachPlanner(PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path))

    stored = await planner.plan(
        ApproachRequest(
            company="Katana",
            fields={},
            dossier=dossier,
            strategy=strategy,
            buyer_roles=["CTO"],
            today=date(2026, 11, 16),
        )
    )

    sent = runner.payloads["outreach-approach-planner"][0].model_dump()
    assert sent["today"] == "2026-11-16"
    assert sent["offer"]["buyer_roles"] == ["CTO"]
    assert [c["ref"] for c in sent["contacts"]][-1] == "inbox"
    assert [t.step for t in stored.plan.touches] == [1]
    assert "less than 2 days" in stored.plan.concerns[0]
    assert stored.contacts[-1].routes[0].address == "hello@katanamrp.com"


async def test_replanning_keeps_done_touches_and_their_options(
    tmp_path: Path, dossier: CompanyDossier, strategy: Strategy
) -> None:
    angle = strategy.outreach_angles[0].name
    first = touch(1, 0, "c1", "c1.email", angle=angle)
    previous = StoredApproach(
        plan=plan(first, touch(2, 4, "c1", "c1.email", angle=angle)),
        contacts=CONTACTS,
        hooks=HOOKS,
        planned_on="2026-11-10",
        done=[DoneTouch(step=1, on=date(2026, 11, 11), interaction_id=7)],
    )
    runner = RecordedRunner(
        {"outreach-approach-planner": [plan(touch(1, 6, "c1", "c1.linkedin", angle=angle))]}
    )
    planner = ApproachPlanner(PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path))

    stored = await planner.plan(
        ApproachRequest(
            company="Katana",
            fields={},
            dossier=dossier,
            strategy=strategy,
            today=date(2026, 11, 16),
            previous=previous,
        )
    )

    sent = runner.payloads["outreach-approach-planner"][0].model_dump()
    assert (sent["days_since_start"], [t["step"] for t in sent["done"]]) == (5, [1])
    assert [c["ref"] for c in sent["contacts"]] == [c.ref for c in CONTACTS]
    assert [(t.step, t.route_ref) for t in stored.plan.touches] == [
        (1, "c1.email"),
        (2, "c1.linkedin"),
    ]
    assert stored.done == previous.done
    assert stored.contacts == CONTACTS
