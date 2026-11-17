"""Approach planning: an agent proposes who to contact and when; Python enforces the rules."""

import logging
import re
from collections import Counter
from collections.abc import Sequence
from datetime import date, timedelta

from pydantic import BaseModel, Field

from mozg_outreach.domain import FieldValue
from mozg_outreach.mail.suppression import Blocklist
from mozg_outreach.naturalize.models import Channel
from mozg_outreach.pipelines.biz.approach_models import (
    ApproachInput,
    ApproachPlan,
    ApproachStep,
    Logged,
    Offer,
    PlanContact,
    PlanHook,
    PlannedTouch,
    PlanRoute,
    StoredApproach,
    TouchChannel,
)
from mozg_outreach.pipelines.biz.enrichment_models import CompanyDossier, KeyPerson
from mozg_outreach.pipelines.biz.letter_briefs import (
    Contact,
    LetterBriefBuilder,
    Route,
)
from mozg_outreach.pipelines.biz.models import Strategy
from mozg_outreach.pipelines.steps import PipelineSteps

logger = logging.getLogger(__name__)

INBOX = "inbox"
MAX_PEOPLE = 3
# Emailing 3+ people at one company costs replies (Hunter 2025); a third goes by LinkedIn or phone.
MAX_EMAILED_PEOPLE = 2
MAX_TOUCHES = 10
MAX_TOUCHES_PER_CONTACT = 7
MAX_EMAILS_PER_CONTACT = 4
# Three or more voicemails cut email replies below none at all (Gong 2024).
MAX_CALLS_PER_CONTACT = 2
# A call supports a letter to the same person on the same day or the day after.
CALL_BESIDE_LETTER_DAYS = 1
MAX_RECYCLED_TOUCHES = 3
STALE_HOOK_DAYS = 90
MIN_GAP_DAYS = 2
# Cold WhatsApp breaks Meta's opt-in rule and UAE cold calls need TDRA approval, so neither opens.
OPENING_CHANNELS = frozenset({"email", "linkedin", "form"})
UAE_DIALLING = ("971", "00971")
TDRA_CONCERN = (
    "Calls to UAE numbers count as telemarketing: they need TDRA approval (Cabinet Resolution "
    "56/2024), 09:00-18:00 only, and no calls to numbers on the Do Not Call Registry."
)
MAX_DAYS = 30
# Calls get talking points, not a letter.
LETTER_CHANNELS: dict[TouchChannel, Channel] = {
    "email": "email",
    "linkedin": "dm",
    # A website form takes a short note the sender pastes in, like a direct message.
    "form": "dm",
}


class ApproachRequest(BaseModel):
    company: str = Field(min_length=1)
    fields: dict[str, FieldValue]
    dossier: CompanyDossier
    strategy: Strategy
    buyer_roles: list[str] = Field(default_factory=list)
    assets: list[str] = Field(default_factory=list)
    today: date
    previous: StoredApproach | None = Field(
        default=None, description="The current plan; its done touches stay as they are."
    )
    logged: list[Logged] = Field(
        default_factory=list, description="The lead's interactions, oldest first."
    )
    blocked: Blocklist = Field(
        default_factory=Blocklist, description="Addresses that bounced or asked to stop."
    )
    feedback: str | None = Field(default=None, description="The user's notes for this plan.")


class SequenceClosedError(ValueError):
    """The company asked not to be contacted, so no new sequence starts."""


# Outcomes after which the company gets no new sequence at all.
FINAL_STOPS = frozenset({"refused", "opted_out"})


class ApproachOptions:
    """The contacts, routes and hooks a plan may use, all taken from the dossier."""

    def __init__(
        self, company: str, dossier: CompanyDossier, blocked: Blocklist | None = None
    ) -> None:
        self._company = company
        self._dossier = dossier
        self._builder = LetterBriefBuilder(company, dossier)
        self._blocked = blocked or Blocklist()
        self.dropped: list[str] = []

    def contacts(self) -> list[PlanContact]:
        people = (
            {p.name: p for p in self._dossier.contacts.key_people} if self._dossier.contacts else {}
        )
        found = [
            PlanContact(
                ref=f"c{i}",
                name=c.name,
                title=c.title,
                relevance=c.relevance,
                routes=_person_routes(f"c{i}", people.get(c.name)),
            )
            for i, c in enumerate(self._builder.contacts(), start=1)
        ]
        inbox = self._inbox()
        return [self._unblocked(c) for c in ([*found, inbox] if inbox.routes else found)]

    def _unblocked(self, contact: PlanContact) -> PlanContact:
        kept = []
        for route in contact.routes:
            why = (
                self._blocked.blocks(route.address, route.confidence == "guessed")
                if route.channel == "email"
                else None
            )
            if why:
                self.dropped.append(f"Left out {route.address}: {why}.")
            else:
                kept.append(route)
        return contact.model_copy(update={"routes": kept})

    def hooks(self, fields: dict[str, FieldValue]) -> list[PlanHook]:
        evidence = self._builder.evidence(fields)
        return [PlanHook(ref=f"h{i}", hook=h) for i, h in enumerate(evidence.hooks, start=1)]

    def _inbox(self) -> PlanContact:
        contacts = self._dossier.contacts
        routes: list[PlanRoute] = []
        if contacts:
            published = [e for e in contacts.emails if e.kind == "published"]
            # A guessed role address is a last resort; the push guards make it confirm first.
            usable = published or [e for e in contacts.emails if e.kind == "pattern_guess"]
            routes += [
                PlanRoute(
                    ref=f"{INBOX}.email{i}",
                    channel="email",
                    address=e.address,
                    confidence="shared_inbox" if e.kind == "published" else "guessed",
                    source_url=e.source_url,
                )
                for i, e in enumerate(usable, start=1)
            ]
            routes += [
                PlanRoute(
                    ref=f"{INBOX}.phone{i}",
                    channel="phone",
                    address=p.number,
                    confidence="published",
                    source_url=p.source_url,
                )
                for i, p in enumerate(contacts.phones, start=1)
            ]
            if contacts.contact_form:
                routes.append(
                    PlanRoute(
                        ref=f"{INBOX}.form",
                        channel="form",
                        address=contacts.contact_form,
                        confidence="published",
                        source_url=contacts.contact_form,
                    )
                )
        return PlanContact(
            ref=INBOX,
            name=None,
            title="Company inbox",
            relevance=f"{self._company}'s published channels; a gatekeeper forwards.",
            routes=routes,
        )


def _person_routes(ref: str, person: KeyPerson | None) -> list[PlanRoute]:
    if person is None:
        return []
    routes: list[PlanRoute] = []
    if person.email:
        guessed = person.email.kind == "pattern_guess"
        routes.append(
            PlanRoute(
                ref=f"{ref}.email",
                channel="email",
                address=person.email.address,
                confidence="guessed" if guessed else "published",
                source_url=person.email.source_url,
            )
        )
    if person.linkedin:
        routes.append(
            PlanRoute(
                ref=f"{ref}.linkedin",
                channel="linkedin",
                address=person.linkedin,
                confidence="linkedin",
                source_url=person.source_url,
            )
        )
    if person.phone:
        routes.append(
            PlanRoute(
                ref=f"{ref}.phone",
                channel="phone",
                address=person.phone.number,
                confidence="published",
                source_url=person.phone.source_url,
            )
        )
    return routes


class ApproachValidator:
    """Drops touches that break the outreach rules; each drop becomes a concern.

    Done touches count toward every rule but are never dropped or renumbered.
    """

    def __init__(
        self,
        contacts: list[PlanContact],
        hooks: list[PlanHook],
        angles: list[str],
        done: Sequence[PlannedTouch] = (),
        today: int | None = None,
        max_touches: int = MAX_TOUCHES,
    ) -> None:
        self._done = list(done)
        self._today = today
        self._max_touches = max_touches
        self._planned: list[PlannedTouch] = []
        self._contacts = {c.ref: c for c in contacts}
        self._routes = {r.ref: (c.ref, r) for c in contacts for r in c.routes}
        self._hooks = {h.ref for h in hooks}
        self._angles = set(angles)
        self._other_than_guessed = any(
            r.confidence != "guessed" and r.channel in OPENING_CHANNELS
            for c in contacts
            for r in c.routes
        )

    def validate(self, plan: ApproachPlan) -> ApproachPlan:
        kept: list[PlannedTouch] = list(self._done)
        concerns = list(plan.concerns)
        self._planned = [*self._done, *plan.touches]
        for touch in sorted(plan.touches, key=lambda t: (t.day, t.step)):
            problem = self._problem(touch, kept)
            if problem:
                logger.info("dropped approach step %d: %s", touch.step, problem)
                concerns.append(f"Dropped step {touch.step}: {problem}.")
            else:
                kept.append(touch)
        # A call supports a letter, so the sequence never ends on one.
        while len(kept) > len(self._done) and self._channel(kept[-1]) == "phone":
            dropped = kept.pop()
            concerns.append(f"Dropped step {dropped.step}: a call can't end the sequence.")
        after = max((t.step for t in self._done), default=0)
        fresh = [
            t.model_copy(update={"step": after + i})
            for i, t in enumerate(kept[len(self._done) :], start=1)
        ]
        if any(self._uae_call(t) for t in fresh):
            concerns.append(TDRA_CONCERN)
        kept = [*self._done, *fresh]
        first = kept[0].contact_ref if kept else plan.first_contact_ref
        if not self._done and kept and first != plan.first_contact_ref:
            concerns.append(f"The first kept touch goes to {first}, not {plan.first_contact_ref}.")
        return plan.model_copy(
            update={"touches": kept, "first_contact_ref": first, "concerns": concerns}
        )

    def _problem(self, touch: PlannedTouch, kept: list[PlannedTouch]) -> str | None:
        owner = self._routes.get(touch.route_ref)
        if touch.contact_ref not in self._contacts:
            return f"unknown contact {touch.contact_ref}"
        if owner is None or owner[0] != touch.contact_ref:
            return f"route {touch.route_ref} is not one of {touch.contact_ref}'s routes"
        route = owner[1]
        if touch.addressed_to is not None and (
            touch.contact_ref != INBOX
            or touch.addressed_to == INBOX
            or touch.addressed_to not in self._contacts
        ):
            return f"addressed_to {touch.addressed_to} needs an inbox touch and a known person"
        if touch.angle not in self._angles:
            return f"unknown angle {touch.angle!r}"
        if touch.hook_ref is not None and touch.hook_ref not in self._hooks:
            return f"unknown hook {touch.hook_ref}"
        if self._today is not None and touch.day < self._today:
            return f"day {touch.day} is already past (today is day {self._today})"
        if touch.day > (self._today or 0) + MAX_DAYS:
            return f"day {touch.day} is past the {MAX_DAYS}-day sequence"
        if len(kept) - len(self._done) >= self._max_touches:
            return f"more than {self._max_touches} touches"
        if touch.connection is not None and route.channel != "linkedin":
            return "a connection request needs a LinkedIn route"
        if not kept and route.channel not in OPENING_CHANNELS:
            return "the first touch must be an email, a LinkedIn message or a contact-form note"
        return self._pacing(touch, kept) or self._route_rules(
            touch, route.channel, route.confidence, kept
        )

    def _pacing(self, touch: PlannedTouch, kept: list[PlannedTouch]) -> str | None:
        own = [t for t in kept if t.contact_ref == touch.contact_ref]
        if self._channel(touch) == "phone":
            calls = [t for t in own if self._channel(t) == "phone"]
            if len(calls) >= MAX_CALLS_PER_CONTACT:
                return f"more than {MAX_CALLS_PER_CONTACT} calls to one contact"
            if not self._beside_letter(touch):
                return "a call needs a letter to the same person that day or the day before"
        else:
            letters = [t for t in own if self._channel(t) != "phone"]
            if letters and touch.day - letters[-1].day < MIN_GAP_DAYS:
                return f"less than {MIN_GAP_DAYS} days after the previous touch to the same contact"
        if any(t.day == touch.day and t.contact_ref != touch.contact_ref for t in kept):
            return "another contact at the same company is touched that day"
        people = {t.contact_ref for t in kept if t.contact_ref != INBOX}
        if (
            touch.contact_ref != INBOX
            and touch.contact_ref not in people
            and len(people) >= MAX_PEOPLE
        ):
            return f"more than {MAX_PEOPLE} people at one company"
        if len(own) >= MAX_TOUCHES_PER_CONTACT:
            return f"more than {MAX_TOUCHES_PER_CONTACT} touches to one contact"
        return None

    def _channel(self, touch: PlannedTouch) -> TouchChannel:
        return self._routes[touch.route_ref][1].channel

    def _beside_letter(self, call: PlannedTouch) -> bool:
        return any(
            t.contact_ref == call.contact_ref
            and t.route_ref in self._routes
            and self._channel(t) != "phone"
            and 0 <= call.day - t.day <= CALL_BESIDE_LETTER_DAYS
            for t in self._planned
        )

    def _uae_call(self, touch: PlannedTouch) -> bool:
        route = self._routes[touch.route_ref][1]
        digits = re.sub(r"\D", "", route.address)
        return route.channel == "phone" and digits.startswith(UAE_DIALLING)

    def _route_rules(
        self, touch: PlannedTouch, channel: str, confidence: str, kept: list[PlannedTouch]
    ) -> str | None:
        own = [self._routes[t.route_ref][1] for t in kept if t.contact_ref == touch.contact_ref]
        # A second note through the same form reads as spam to whoever triages it.
        if channel == "form" and any(self._channel(t) == "form" for t in kept):
            return "more than one contact-form note"
        if channel == "email" and sum(r.channel == "email" for r in own) >= MAX_EMAILS_PER_CONTACT:
            return f"more than {MAX_EMAILS_PER_CONTACT} emails to one contact"
        emailed = {t.contact_ref for t in kept if self._channel(t) == "email"}
        if (
            channel == "email"
            and touch.contact_ref not in emailed
            and len(emailed) >= MAX_EMAILED_PEOPLE
        ):
            return f"more than {MAX_EMAILED_PEOPLE} people at one company by email"
        if confidence == "guessed":
            if not kept and self._other_than_guessed:
                return "a guessed address can't open the sequence while a published route exists"
            guessed = {
                self._routes[t.route_ref][1].address
                for t in kept
                if self._routes[t.route_ref][1].confidence == "guessed"
            }
            if guessed - {self._routes[touch.route_ref][1].address}:
                return "only one guessed address per company"
        return None


class ApproachPlanner:
    def __init__(self, steps: PipelineSteps) -> None:
        self._steps = steps

    async def plan(self, request: ApproachRequest) -> StoredApproach:
        previous = request.previous
        history = previous.history if previous else []
        ended: ApproachStep | None = None
        if previous is not None:
            position = previous.current(request.logged, request.today)
            if _over(previous, position):
                refuse_closed(request.company, request.logged)
                history = [*history, previous.ended(position.reason or "finished without a reply")]
                ended, previous = position, None
        done = previous.done_touches() if previous else []
        left_out: list[str] = []
        if previous and done:
            # Done touches point at these refs, so a started sequence keeps its options.
            contacts, hooks = previous.contacts, previous.hooks
        else:
            options = ApproachOptions(request.company, request.dossier, request.blocked)
            contacts, hooks = options.contacts(), options.hooks(request.fields)
            left_out = options.dropped
        started = previous.started_on() if previous else None
        today = (request.today - started).days if started else None
        strategy = request.strategy
        payload = ApproachInput(
            company=request.company,
            sector=_text(request.fields, "sector"),
            location=_text(request.fields, "location"),
            size=request.dossier.growth.company_size.estimate if request.dossier.growth else None,
            today=request.today.isoformat(),
            offer=Offer(
                offering=strategy.goal_analysis.offering,
                target_audience=strategy.goal_analysis.target_audience,
                buyer_roles=request.buyer_roles,
                assets=request.assets,
            ),
            angles=strategy.outreach_angles,
            contacts=contacts,
            hooks=hooks,
            done=done,
            days_since_start=today,
            history=history,
            feedback=request.feedback,
        )
        raw = await self._steps.run("biz", "outreach-approach-planner", payload, ApproachPlan)
        angles = [a.name for a in strategy.outreach_angles]
        limit = MAX_RECYCLED_TOUCHES if history else MAX_TOUCHES
        plan = ApproachValidator(contacts, hooks, angles, done, today, limit).validate(raw)
        stale = _stale_openers(plan, hooks, request.today, len(done))
        rest = ended.recycle_on if ended else None
        if rest is not None and request.today < rest:
            stale.append(f"Re-planned before the rest ended on {rest}.")
        plan = plan.model_copy(update={"concerns": [*plan.concerns, *left_out, *stale]})
        reached = Counter(t.contact_ref for t in plan.touches)
        logger.info(
            "approach for %s: %d touches to %s", request.company, len(plan.touches), dict(reached)
        )
        return StoredApproach(
            plan=plan,
            contacts=contacts,
            hooks=hooks,
            planned_on=request.today.isoformat(),
            done=previous.done if previous and done else [],
            history=history,
        )


def _over(previous: StoredApproach, position: ApproachStep) -> bool:
    """The last sequence reached its end or a stop after it started."""
    return bool(previous.done) and position.state in ("done", "stopped")


def refuse_closed(company: str, logged: Sequence[Logged]) -> None:
    """Raises when the company ever refused or asked not to be contacted."""
    final = [e for e in logged if e.kind in FINAL_STOPS]
    if final:
        last = final[-1]
        what = last.kind.replace("_", " ")
        raise SequenceClosedError(f"{company}: {what} on {last.on}; nothing more goes out")


def _stale_openers(plan: ApproachPlan, hooks: list[PlanHook], today: date, done: int) -> list[str]:
    """Flags new opening touches whose dated hook is over 90 days old."""
    dated = {h.ref: h.hook.date for h in hooks if h.hook.date}
    concerns: list[str] = []
    for touch in plan.touches[done:]:
        when = _day(dated.get(touch.hook_ref or ""))
        if touch.purpose == "open" and when and today - when > timedelta(days=STALE_HOOK_DAYS):
            concerns.append(
                f"Step {touch.step} opens on a fact from {when:%Y-%m}, over {STALE_HOOK_DAYS} "
                "days old: say it as context, not news."
            )
    return concerns


def _day(text: str | None) -> date | None:
    """A YYYY-MM-DD or YYYY-MM date, reading a bare month as its first day."""
    if not text:
        return None
    parts = text.strip()[:10].split("-")
    try:
        year, month = int(parts[0]), int(parts[1])
        day = int(parts[2]) if len(parts) > 2 else 1
        return date(year, month, day)
    except (ValueError, IndexError):
        return None


def _text(fields: dict[str, FieldValue], name: str) -> str | None:
    value = fields[name].value if name in fields else None
    return value if isinstance(value, str) else None


class ReaderTouch(BaseModel):
    touch: PlannedTouch
    planned: PlanRoute
    letter: Route | None = Field(description="Where its letter goes; null for a call.")
    in_thread: bool = Field(description="An email to an address an earlier email went to.")


class ReaderSequence(BaseModel):
    """Every touch that reaches one person (or the company inbox), in step order."""

    contact: Contact
    named: bool = Field(description="False for the company inbox with no person to reach.")
    touches: list[ReaderTouch]


def planned_sequences(approach: StoredApproach, company: str) -> list[ReaderSequence]:
    contacts = {c.ref: c for c in approach.contacts}
    routes = {r.ref: r for c in approach.contacts for r in c.routes}
    sequences: dict[str, ReaderSequence] = {}
    for touch in sorted(approach.plan.touches, key=lambda t: t.step):
        reader_ref = touch.addressed_to or touch.contact_ref
        reader = contacts[reader_ref]
        sequence = sequences.setdefault(
            reader_ref,
            ReaderSequence(
                contact=Contact(
                    name=reader.name or f"{company} team",
                    title=reader.title,
                    relevance=reader.relevance,
                    linkedin=next(
                        (r.address for r in reader.routes if r.channel == "linkedin"), None
                    ),
                ),
                named=reader.name is not None,
                touches=[],
            ),
        )
        route = routes[touch.route_ref]
        channel = LETTER_CHANNELS.get(route.channel)
        emailed = {t.planned.address for t in sequence.touches if t.planned.channel == "email"}
        sequence.touches.append(
            ReaderTouch(
                touch=touch,
                planned=route,
                letter=Route(
                    channel=channel,
                    address=route.address,
                    confidence=route.confidence,
                    generic_inbox=touch.contact_ref == INBOX,
                )
                # A request without a note has nothing to write; the user clicks Connect.
                if channel and touch.connection != "no_note"
                else None,
                in_thread=route.channel == "email" and route.address in emailed,
            )
        )
    return list(sequences.values())


def planned_hook(approach: StoredApproach, touch: PlannedTouch) -> PlanHook | None:
    return next((h for h in approach.hooks if h.ref == touch.hook_ref), None)
