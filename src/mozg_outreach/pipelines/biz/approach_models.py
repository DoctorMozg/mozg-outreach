"""Contracts for the approach planner: who to contact, through which route, in what order."""

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from mozg_outreach.letters.models import Hook, TouchPurpose
from mozg_outreach.pipelines.biz.models import CONCERNS_DESCRIPTION, OutreachAngle

TouchChannel = Literal["email", "linkedin", "phone", "form"]
RouteConfidence = Literal["published", "guessed", "linkedin", "shared_inbox"]
SequenceState = Literal["active", "paused", "stopped", "done"]

# Logged outcomes that end a sequence, with how the badge says it.
STOPS = {
    "replied": "replied",
    "refused": "refused",
    "declined": "declined by you",
    "opted_out": "asked not to be contacted",
    "not_now": "said not now",
    "bounced": "bounced",
}
OUT_OF_OFFICE = "out_of_office"
NOT_NOW = "not_now"
# A silent company rests this long before a new sequence (outreach research, 2026-11-17).
RECYCLE_DAYS = 90
RESUME_BUSINESS_DAYS = 2
# An out-of-office without a return date pauses about two working weeks.
UNDATED_PAUSE_DAYS = 14


class PlanRoute(BaseModel):
    ref: str = Field(description="Stable id such as c1.email; touches point at it.")
    channel: TouchChannel
    address: str
    confidence: RouteConfidence
    source_url: str | None = None


class PlanContact(BaseModel):
    ref: str = Field(description="c1, c2... for people; inbox for the company's own channels.")
    name: str | None = Field(description="Null for the company inbox.")
    title: str
    relevance: str
    routes: list[PlanRoute]


class PlanHook(BaseModel):
    ref: str
    hook: Hook


class Offer(BaseModel):
    offering: str
    target_audience: str
    buyer_roles: list[str]
    assets: list[str] = Field(
        default_factory=list, description="Things the sender can send; asks offer only these."
    )


class PlannedTouch(BaseModel):
    step: int = Field(ge=1)
    day: int = Field(ge=0, description="Days after the first touch.")
    contact_ref: str
    route_ref: str
    addressed_to: str | None = Field(
        default=None, description="For an inbox touch, the person ref it should reach; else null."
    )
    purpose: TouchPurpose
    angle: str = Field(description="Name of one input angle.")
    hook_ref: str | None = Field(description="The input hook this touch opens on; null if none.")
    ask: str = Field(
        description="The single ask, e.g. 'Worth a look?'; no meeting on a first letter."
    )
    talking_points: str = Field(description="What this touch says that the earlier ones didn't.")


class PastSequence(BaseModel):
    """An earlier sequence to this company that ended without a conversation."""

    planned_on: str
    ended: str = Field(description="How it ended, e.g. 'finished without a reply'.")
    angles: list[str] = Field(description="Angles its touches used.")
    people: list[str] = Field(description="Who it reached, by name or, for the inbox, title.")


class ApproachInput(BaseModel):
    company: str
    sector: str | None
    location: str | None
    size: str | None = Field(description="Headcount estimate, e.g. 10-50; null if unknown.")
    today: str = Field(description="ISO date the plan starts from.")
    offer: Offer
    angles: list[OutreachAngle]
    contacts: list[PlanContact]
    hooks: list[PlanHook]
    done: list[PlannedTouch] = Field(
        default_factory=list, description="Touches already made; plan only what comes after."
    )
    days_since_start: int | None = Field(
        default=None, description="Days since the first done touch; null before any is done."
    )
    history: list[PastSequence] = Field(
        default_factory=list,
        description="Earlier sequences that ended without a reply; a new one must differ.",
    )


class ApproachPlan(BaseModel):
    summary: str = Field(description="Two or three sentences: who first, why, and the arc.")
    first_contact_ref: str
    rationale: str = Field(description="Why this person and channel lead.")
    touches: list[PlannedTouch]
    avoid: list[str] = Field(description="Things not to say or do with this company.")
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


class DoneTouch(BaseModel):
    step: int
    on: date
    interaction_id: int | None = Field(
        default=None, description="The interaction logged for it; null when Gmail logged it."
    )


class Logged(BaseModel):
    """An interaction on the lead, as the sequence reads it."""

    kind: str
    on: date
    until: date | None = None


class ApproachStep(BaseModel):
    """Where a company's sequence stands: the first touch not done yet, or why it stopped."""

    step: int | None = Field(description="Null when every touch is done.")
    total: int
    done: int
    channel: TouchChannel | None
    reader: str | None
    due: date | None = Field(description="First touch's date plus this touch's day offset.")
    state: SequenceState = "active"
    reason: str | None = Field(default=None, description="Why it stopped or paused, with when.")
    resume_on: date | None = Field(default=None, description="When a paused sequence goes on.")
    recycle_on: date | None = Field(
        default=None, description="When a stopped-for-now or finished sequence may be re-planned."
    )


class StoredApproach(BaseModel):
    """What the item keeps: the validated plan next to the options it was planned from."""

    plan: ApproachPlan
    contacts: list[PlanContact]
    hooks: list[PlanHook]
    planned_on: str
    done: list[DoneTouch] = Field(default_factory=list)
    history: list[PastSequence] = Field(default_factory=list)

    def touch(self, step: int) -> PlannedTouch | None:
        return next((t for t in self.plan.touches if t.step == step), None)

    def done_touches(self) -> list[PlannedTouch]:
        steps = {d.step for d in self.done}
        return [t for t in self.plan.touches if t.step in steps]

    def started_on(self) -> date | None:
        """The day the sequence's first touch was made, if it was."""
        first = min(self.plan.touches, key=lambda t: t.step, default=None)
        return next((d.on for d in self.done if first and d.step == first.step), None)

    def ended(self, how: str) -> PastSequence:
        """This sequence, summed up for the next one's planner."""
        made = self.done_touches()
        return PastSequence(
            planned_on=self.planned_on,
            ended=how,
            angles=sorted({t.angle for t in made}),
            people=sorted({self.reader(t) for t in made}),
        )

    def reader(self, touch: PlannedTouch) -> str:
        contacts = {c.ref: c for c in self.contacts}
        reader = contacts.get(touch.addressed_to or touch.contact_ref)
        if reader is None:
            return touch.addressed_to or touch.contact_ref
        return reader.name or reader.title

    def current(self, logged: Sequence[Logged] = (), today: date | None = None) -> ApproachStep:
        """The next touch, stopped by a reply or the like since the first touch, or paused."""
        done = {d.step for d in self.done}
        ahead = sorted((t for t in self.plan.touches if t.step not in done), key=lambda t: t.step)
        total = len(self.plan.touches)
        started = self.started_on()
        since = [e for e in logged if started is not None and e.on >= started]
        stop = next((e for e in reversed(since) if e.kind in STOPS), None)
        if stop is not None:
            recycle = (
                (stop.until or stop.on + timedelta(days=RECYCLE_DAYS))
                if stop.kind == NOT_NOW
                else None
            )
            return ApproachStep(
                step=None,
                total=total,
                done=total - len(ahead),
                channel=None,
                reader=None,
                due=None,
                state="stopped",
                reason=f"{STOPS[stop.kind]} on {stop.on.isoformat()}",
                recycle_on=recycle,
            )
        if not ahead:
            last = max((d.on for d in self.done), default=None)
            return ApproachStep(
                step=None,
                total=total,
                done=total,
                channel=None,
                reader=None,
                due=None,
                state="done",
                recycle_on=last + timedelta(days=RECYCLE_DAYS) if last else None,
            )
        touch = ahead[0]
        routes = {r.ref: r for c in self.contacts for r in c.routes}
        route = routes.get(touch.route_ref)
        due = started + timedelta(days=touch.day) if started else None
        step = ApproachStep(
            step=touch.step,
            total=total,
            done=total - len(ahead),
            channel=route.channel if route else None,
            reader=self.reader(touch),
            due=due,
        )
        away = next((e for e in reversed(since) if e.kind == OUT_OF_OFFICE), None)
        if away is None:
            return step
        resume = (
            add_business_days(away.until, RESUME_BUSINESS_DAYS)
            if away.until
            else away.on + timedelta(days=UNDATED_PAUSE_DAYS)
        )
        if resume <= (today or date.today()):
            return step
        back = f"back {away.until.isoformat()}" if away.until else "no return date"
        return step.model_copy(
            update={
                "state": "paused",
                "reason": f"out of office ({back})",
                "resume_on": resume,
                "due": max(due, resume) if due else resume,
            }
        )


def add_business_days(day: date, count: int) -> date:
    """`count` Monday-to-Friday days after `day`."""
    while count:
        day += timedelta(days=1)
        if day.weekday() < 5:
            count -= 1
    return day
