"""Contracts for the approach planner: who to contact, through which route, in what order."""

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from mozg_outreach.letters.models import Hook, TouchPurpose
from mozg_outreach.pipelines.biz.models import CONCERNS_DESCRIPTION, OutreachAngle

TouchChannel = Literal["email", "linkedin", "phone"]
RouteConfidence = Literal["published", "guessed", "linkedin", "shared_inbox"]


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
    ask: str = Field(description="The single ask, e.g. 'Worth a 15-minute look?'.")
    talking_points: str = Field(description="What this touch says that the earlier ones didn't.")


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


class ApproachStep(BaseModel):
    """Where a company's sequence stands: the first touch not done yet."""

    step: int | None = Field(description="Null when every touch is done.")
    total: int
    done: int
    channel: TouchChannel | None
    reader: str | None
    due: date | None = Field(description="First touch's date plus this touch's day offset.")


class StoredApproach(BaseModel):
    """What the item keeps: the validated plan next to the options it was planned from."""

    plan: ApproachPlan
    contacts: list[PlanContact]
    hooks: list[PlanHook]
    planned_on: str
    done: list[DoneTouch] = Field(default_factory=list)

    def touch(self, step: int) -> PlannedTouch | None:
        return next((t for t in self.plan.touches if t.step == step), None)

    def done_touches(self) -> list[PlannedTouch]:
        steps = {d.step for d in self.done}
        return [t for t in self.plan.touches if t.step in steps]

    def started_on(self) -> date | None:
        """The day the sequence's first touch was made, if it was."""
        first = min(self.plan.touches, key=lambda t: t.step, default=None)
        return next((d.on for d in self.done if first and d.step == first.step), None)

    def reader(self, touch: PlannedTouch) -> str:
        contacts = {c.ref: c for c in self.contacts}
        reader = contacts.get(touch.addressed_to or touch.contact_ref)
        if reader is None:
            return touch.addressed_to or touch.contact_ref
        return reader.name or reader.title

    def current(self) -> ApproachStep:
        done = {d.step for d in self.done}
        ahead = sorted((t for t in self.plan.touches if t.step not in done), key=lambda t: t.step)
        total = len(self.plan.touches)
        if not ahead:
            return ApproachStep(
                step=None, total=total, done=total, channel=None, reader=None, due=None
            )
        touch = ahead[0]
        routes = {r.ref: r for c in self.contacts for r in c.routes}
        route = routes.get(touch.route_ref)
        started = self.started_on()
        return ApproachStep(
            step=touch.step,
            total=total,
            done=total - len(ahead),
            channel=route.channel if route else None,
            reader=self.reader(touch),
            due=started + timedelta(days=touch.day) if started else None,
        )
