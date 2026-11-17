"""Write naturalized letters to the people of one enriched company."""

import asyncio
import logging

from pydantic import BaseModel, Field

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.domain import FieldValue
from mozg_outreach.letters.composer import Composition, Delivery, LetterComposer, WrittenLetter
from mozg_outreach.letters.frame import LetterFrame, LetterParty
from mozg_outreach.letters.models import (
    Angle,
    EarlierTouch,
    Hook,
    LetterBrief,
    LetterRecipient,
    ProfilerInput,
    ReaderStyle,
    RevisionTarget,
    SenderSettings,
    Skipped,
    TouchContext,
    TouchPurpose,
)
from mozg_outreach.letters.sameness import Phrasing
from mozg_outreach.naturalize.models import Channel, WordBudget
from mozg_outreach.naturalize.naturalizer import WORD_BUDGETS, Naturalizer, VoiceContext
from mozg_outreach.pipelines.biz.approach import ReaderSequence, planned_hook, planned_sequences
from mozg_outreach.pipelines.biz.approach_models import StoredApproach
from mozg_outreach.pipelines.biz.enrichment_models import CompanyDossier
from mozg_outreach.pipelines.biz.letter_briefs import (
    Addressee,
    CompanyEvidence,
    Contact,
    LetterBriefBuilder,
)
from mozg_outreach.pipelines.steps import PipelineSteps

logger = logging.getLogger(__name__)

# Company letters stay in English until a brief names its market's language.
BIZ_LANGUAGE = "en"

SUBJECT_MAX_CHARS = 55
# Three openings per letter; a separate judge keeps the best (user's choice, 2026-11-17).
CANDIDATES = 3
# Follow-ups add one new thing and a nudge; a breakup is a short last note (outreach research).
TOUCH_BUDGETS: dict[TouchPurpose, WordBudget] = {
    "follow_up": WordBudget(min=40, max=110),
    "breakup": WordBudget(min=30, max=70),
}


class RegionRules(BaseModel):
    """What the company's location changes about every letter to it."""

    gulf: bool = Field(default=False, description="Opt-out on every letter, and no casual 'Hi'.")
    privacy_url: str | None = Field(
        default=None, description="EU and UK first emails point to the sender's privacy notice."
    )
    notes: list[str] = Field(default_factory=list, description="Shown beside every letter.")


class LetterRequest(BaseModel):
    company: str = Field(min_length=1)
    fields: dict[str, FieldValue]
    dossier: CompanyDossier
    angles: list[Angle] = Field(min_length=1)
    sender: SenderSettings
    background: str | None = Field(default=None, description="Facts about the sender's company.")
    voices: dict[Channel, VoiceContext] = Field(default_factory=dict)
    revision: RevisionTarget | None = Field(default=None, description="Rewrite only this letter.")
    approach: StoredApproach | None = Field(
        default=None, description="When set, letters go to the plan's contacts with its angles."
    )
    region: RegionRules = Field(default_factory=RegionRules)
    offers: list[str] = Field(
        default_factory=list, description="Things the sender can send; asks offer only these."
    )
    recent: list[Phrasing] = Field(
        default_factory=list, description="The project's recent letters to other companies."
    )
    existing: dict[int, str] = Field(
        default_factory=dict,
        description="Newest draft text per plan step, which letters not rewritten now still say.",
    )


class LetterBatch(BaseModel):
    letters: list[WrittenLetter]
    skipped: list[Skipped]


class BizLetterWriter:
    def __init__(
        self, steps: PipelineSteps, naturalizer: Naturalizer, candidates: int = CANDIDATES
    ) -> None:
        self._steps = steps
        self._composer = LetterComposer(steps, naturalizer)
        self._candidates = candidates

    async def write(self, request: LetterRequest) -> LetterBatch:
        evidence = LetterBriefBuilder(request.company, request.dossier).evidence(request.fields)
        target = request.revision
        # A letter written before steps existed is rewritten on the routing it was written for.
        if request.approach is not None and (target is None or target.step is not None):
            return await self._planned(request, evidence, request.approach)
        return await self._unplanned(request, evidence)

    async def _planned(
        self, request: LetterRequest, evidence: CompanyEvidence, approach: StoredApproach
    ) -> LetterBatch:
        """Letters for the plan's steps; a reader's go in step order so each sees the one before."""
        sequences = planned_sequences(approach, request.company)
        done = {d.step for d in approach.done}
        due = {
            t.touch.step
            for s in sequences
            for t in s.touches
            if t.letter is not None and t.touch.step not in done
        }
        target = request.revision
        if target is not None:
            match = [
                t.touch.step
                for s in sequences
                for t in s.touches
                if t.letter is not None
                and (t.touch.step, s.contact.name, t.letter.channel)
                == (target.step, target.recipient, target.channel)
            ]
            if not match:
                reason = f"step {target.step} is no longer in the approach plan"
                return LetterBatch(
                    letters=[], skipped=[Skipped(contact=target.recipient, reason=reason)]
                )
            due = set(match)
        if not evidence.hooks:
            return _no_hooks([s.contact.name for s in sequences if _writes(s, due)])
        results = await asyncio.gather(
            *(self._sequence(request, evidence, s, due) for s in sequences if _writes(s, due))
        )
        found = [r for batch in results for r in batch]
        return _batch(request.company, found, [])

    async def _sequence(
        self,
        request: LetterRequest,
        evidence: CompanyEvidence,
        sequence: ReaderSequence,
        due: set[int],
    ) -> list[WrittenLetter | Skipped]:
        assert request.approach is not None
        # The company inbox has no one to profile.
        style = await self._profile(sequence.contact, request.company) if sequence.named else None
        approach = request.approach
        total = len(approach.plan.touches)
        first_step = sequence.touches[0].touch.step if sequence.touches else 0
        others = [
            approach.reader(t)
            for t in sorted(approach.plan.touches, key=lambda t: t.step)
            if t.step < first_step and approach.reader(t) != sequence.contact.name
        ]
        earlier: list[EarlierTouch] = []
        written: list[WrittenLetter | Skipped] = []
        for planned in sequence.touches:
            touch = planned.touch
            text = request.existing.get(touch.step)
            if planned.letter is not None and touch.step in due:
                context = TouchContext(
                    step=touch.step,
                    total=total,
                    day=touch.day,
                    purpose=touch.purpose,
                    talking_points=touch.talking_points,
                    in_thread=planned.in_thread,
                    earlier=list(earlier),
                    others_contacted=list(dict.fromkeys(others)) if not earlier else [],
                )
                addressee = Addressee(
                    contact=sequence.contact,
                    route=planned.letter,
                    touch=touch,
                    named=sequence.named,
                )
                letter = await self._letter(request, evidence, addressee, style, context)
                written.append(letter)
                if isinstance(letter, WrittenLetter):
                    text = letter.text
            earlier.append(
                EarlierTouch(
                    step=touch.step,
                    day=touch.day,
                    channel=planned.planned.channel,
                    purpose=touch.purpose,
                    talking_points=touch.talking_points,
                    text=text if planned.letter is not None else None,
                )
            )
        return written

    async def _unplanned(self, request: LetterRequest, evidence: CompanyEvidence) -> LetterBatch:
        """One letter per reachable contact on the dossier's default routing."""
        recipients = LetterBriefBuilder(request.company, request.dossier).recipients()
        target = request.revision
        if target is not None:
            addressees = [
                a
                for a in recipients.addressees
                if (a.contact.name, a.route.channel) == (target.recipient, target.channel)
            ]
            if not addressees:
                reason = f"no {target.channel} route to them in the dossier any more"
                return LetterBatch(
                    letters=[], skipped=[Skipped(contact=target.recipient, reason=reason)]
                )
            recipients = recipients.model_copy(update={"addressees": addressees, "skipped": []})
        if not evidence.hooks:
            batch = _no_hooks([a.contact.name for a in recipients.addressees])
            return batch.model_copy(update={"skipped": [*recipients.skipped, *batch.skipped]})
        results = await asyncio.gather(
            *(self._unplanned_letter(request, evidence, a) for a in recipients.addressees)
        )
        return _batch(request.company, list(results), recipients.skipped)

    async def _unplanned_letter(
        self, request: LetterRequest, evidence: CompanyEvidence, addressee: Addressee
    ) -> WrittenLetter | Skipped:
        style = await self._profile(addressee.contact, request.company)
        return await self._letter(request, evidence, addressee, style, None)

    async def _letter(
        self,
        request: LetterRequest,
        evidence: CompanyEvidence,
        addressee: Addressee,
        style: ReaderStyle | None,
        touch: TouchContext | None,
    ) -> WrittenLetter | Skipped:
        contact, route = addressee.contact, addressee.route
        angles, hooks, ask = _plan_choices(request, evidence, addressee)
        threaded = touch is not None and touch.in_thread
        brief = LetterBrief(
            channel=route.channel,
            language=BIZ_LANGUAGE,
            recipient=LetterRecipient(
                name=contact.name,
                title=contact.title,
                company=request.company,
                relevance=contact.relevance,
            ),
            generic_inbox=route.generic_inbox,
            word_budget=_budget(route.channel, touch),
            # A reply in the thread takes the sent letter's subject.
            subject_max_chars=SUBJECT_MAX_CHARS
            if route.channel == "email" and not threaded
            else None,
            angles=angles,
            hooks=hooks,
            verified_entities=evidence.verified_entities,
            reader_style=style,
            sender_voice=request.sender.voice,
            sender_background=request.background,
            ask=ask,
            offers=request.offers,
            touch=touch,
            avoid_phrasings=_phrasings(request.recent),
            revision=request.revision.revision if request.revision else None,
        )
        guessed = route.confidence == "guessed"
        return await self._composer.compose(
            Composition(
                brief=brief,
                frame=_frame(request, addressee, touch),
                voice=request.voices.get(route.channel, VoiceContext(profile=None, samples=[])),
                delivery=Delivery(
                    address=route.address,
                    confidence=route.confidence,
                    note=f"{route.address} is a guess from the company's address pattern."
                    if guessed
                    else None,
                ),
                calibration=_calibration(style),
                notes=request.region.notes,
                recent=request.recent,
                candidates=self._candidates,
            )
        )

    async def _profile(self, contact: Contact, company: str) -> ReaderStyle | None:
        payload = ProfilerInput(
            name=contact.name,
            title=contact.title,
            company=company,
            linkedin=contact.linkedin,
            seed_signals=[],
        )
        try:
            return await self._steps.run(
                "biz", "outreach-personality-profiler", payload, ReaderStyle
            )
        except (AgentRunError, AgentOutputError) as exc:
            # Without a read the letter keeps a neutral shape; that is the documented fallback.
            logger.warning("profiler failed for %s: %s", contact.name, exc)
            return None


def _phrasings(recent: list[Phrasing]) -> list[str]:
    lines = [p.opening for p in recent] + [p.ask for p in recent if p.ask]
    return list(dict.fromkeys(lines))


def _frame(request: LetterRequest, addressee: Addressee, touch: TouchContext | None) -> LetterFrame:
    """Salutation and sign-off; the first email to each reader offers to stop, and emails sign
    with the sender's card."""
    route, region = addressee.route, request.region
    party = LetterParty(
        recipient=addressee.contact.name if addressee.named else None, sender=request.sender.name
    )
    frame = LetterFrame.build(
        route.channel, request.sender.tone, party, BIZ_LANGUAGE, casual_hi=not region.gulf
    )
    emailed = touch is not None and any(e.channel == "email" for e in touch.earlier)
    first_email = route.channel == "email" and not emailed
    if first_email or region.gulf:
        frame = frame.with_opt_out(BIZ_LANGUAGE, region.privacy_url if first_email else None)
    if route.channel == "email":
        frame = frame.model_copy(update={"card": request.sender.card})
    return frame


def _writes(sequence: ReaderSequence, due: set[int]) -> bool:
    return any(t.letter is not None and t.touch.step in due for t in sequence.touches)


def _no_hooks(names: list[str]) -> LetterBatch:
    # A cold letter without a verified opener is the generic kind nobody answers.
    reason = "no verified fact about the company to open with"
    return LetterBatch(letters=[], skipped=[Skipped(contact=n, reason=reason) for n in names])


def _batch(
    company: str, results: list[WrittenLetter | Skipped], skipped: list[Skipped]
) -> LetterBatch:
    letters = [r for r in results if isinstance(r, WrittenLetter)]
    skipped = [*skipped, *(r for r in results if isinstance(r, Skipped))]
    logger.info("letters for %s: %d written, %d skipped", company, len(letters), len(skipped))
    return LetterBatch(letters=letters, skipped=skipped)


def _budget(channel: Channel, touch: TouchContext | None) -> WordBudget:
    if touch is None or touch.purpose == "open":
        return WORD_BUDGETS[channel]
    return TOUCH_BUDGETS[touch.purpose]


def _calibration(style: ReaderStyle | None) -> str:
    if style is None:
        return "neutral: the profiler failed"
    if style.social_style is None:
        return f"role priorities: {', '.join(style.role_priorities) or 'none'}"
    return f"{style.social_style} ({style.confidence} confidence)"


def _plan_choices(
    request: LetterRequest, evidence: CompanyEvidence, addressee: Addressee
) -> tuple[list[Angle], list[Hook], str | None]:
    """The plan's angle, hook and ask for this touch; without a plan, everything known."""
    touch = addressee.touch
    if touch is None or request.approach is None:
        return request.angles, evidence.hooks, None
    angles = [a for a in request.angles if a.name == touch.angle] or request.angles
    hook = planned_hook(request.approach, touch)
    return angles, [hook.hook] if hook else evidence.hooks, touch.ask
