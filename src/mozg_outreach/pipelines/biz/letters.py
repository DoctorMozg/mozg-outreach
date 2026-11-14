"""Write naturalized letters to the people of one enriched company."""

import asyncio
import logging

from pydantic import BaseModel, Field

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.domain import FieldValue
from mozg_outreach.letters.checks import LetterChecker, LetterProblem
from mozg_outreach.letters.frame import LetterFrame, LetterParty
from mozg_outreach.letters.models import (
    Angle,
    DraftReview,
    LetterBrief,
    LetterDraft,
    LetterRecipient,
    ProfilerInput,
    ReaderStyle,
    Tone,
)
from mozg_outreach.naturalize.models import Channel, RetryFeedback
from mozg_outreach.naturalize.naturalizer import (
    WORD_BUDGETS,
    Naturalizer,
    NaturalizeRequest,
    VoiceContext,
)
from mozg_outreach.pipelines.biz.enrichment_models import CompanyDossier
from mozg_outreach.pipelines.biz.letter_briefs import (
    Addressee,
    CompanyEvidence,
    LetterBriefBuilder,
    Skipped,
)
from mozg_outreach.pipelines.steps import PipelineSteps

logger = logging.getLogger(__name__)

SUBJECT_MAX_CHARS = 55
MAX_DRAFT_ATTEMPTS = 2


class SenderSettings(BaseModel):
    name: str = Field(min_length=1, description="From the project settings; signs every letter.")
    tone: Tone = "neutral"
    voice: str = Field(default="", description="The strategy's sender voice description.")


class LetterRequest(BaseModel):
    company: str = Field(min_length=1)
    fields: dict[str, FieldValue]
    dossier: CompanyDossier
    angles: list[Angle] = Field(min_length=1)
    sender: SenderSettings
    voices: dict[Channel, VoiceContext] = Field(default_factory=dict)


class WrittenLetter(BaseModel):
    recipient: str
    channel: Channel
    subject: str | None
    text: str
    review: DraftReview


class LetterBatch(BaseModel):
    letters: list[WrittenLetter]
    skipped: list[Skipped]


class _Attempt(BaseModel):
    draft: LetterDraft
    text: str
    problems: list[LetterProblem]


class BizLetterWriter:
    def __init__(self, steps: PipelineSteps, naturalizer: Naturalizer) -> None:
        self._steps = steps
        self._naturalizer = naturalizer

    async def write(self, request: LetterRequest) -> LetterBatch:
        builder = LetterBriefBuilder(request.company, request.dossier)
        evidence = builder.evidence(request.fields)
        recipients = builder.recipients()
        if not evidence.hooks:
            # A cold letter without a verified opener is the generic kind nobody answers.
            reason = "no verified fact about the company to open with"
            skipped = [
                Skipped(contact=a.contact.name, reason=reason) for a in recipients.addressees
            ]
            return LetterBatch(letters=[], skipped=[*recipients.skipped, *skipped])
        results = await asyncio.gather(
            *(self._letter(request, evidence, a) for a in recipients.addressees)
        )
        letters = [r for r in results if isinstance(r, WrittenLetter)]
        skipped = [*recipients.skipped, *(r for r in results if isinstance(r, Skipped))]
        logger.info(
            "letters for %s: %d written, %d skipped", request.company, len(letters), len(skipped)
        )
        return LetterBatch(letters=letters, skipped=skipped)

    async def _letter(
        self, request: LetterRequest, evidence: CompanyEvidence, addressee: Addressee
    ) -> WrittenLetter | Skipped:
        contact, route = addressee.contact, addressee.route
        style = await self._profile(addressee, request.company)
        brief = LetterBrief(
            channel=route.channel,
            recipient=LetterRecipient(
                name=contact.name,
                title=contact.title,
                company=request.company,
                relevance=contact.relevance,
            ),
            generic_inbox=route.generic_inbox,
            word_budget=WORD_BUDGETS[route.channel],
            subject_max_chars=SUBJECT_MAX_CHARS if route.channel == "email" else None,
            angles=request.angles,
            hooks=evidence.hooks,
            verified_entities=evidence.verified_entities,
            reader_style=style,
            sender_voice=request.sender.voice,
        )
        party = LetterParty(recipient=contact.name, sender=request.sender.name)
        frame = LetterFrame.build(route.channel, request.sender.tone, party)
        checker = LetterChecker(brief, frame)
        try:
            attempt = await self._draft(brief, frame, checker)
        except (AgentRunError, AgentOutputError) as exc:
            logger.warning("copywriter failed for %s: %s", contact.name, exc)
            return Skipped(contact=contact.name, reason=f"copywriter failed: {exc}")
        voice = request.voices.get(route.channel, VoiceContext(profile=None, samples=[]))
        preserve = [contact.name, request.company]
        preserve += [e for e in evidence.verified_entities if e and e in attempt.text]
        outcome = await self._naturalizer.naturalize(
            NaturalizeRequest(text=attempt.text, channel=route.channel, preserve=preserve), voice
        )
        subject = attempt.draft.subject
        notes = [*attempt.draft.concerns, *outcome.review_notes]
        if outcome.reverted and outcome.damage:
            damage = "; ".join(p.detail for p in outcome.damage)
            notes.append(f"Naturalized version rejected, original kept: {damage}")
        names = checker.unverified_names(outcome.text)
        if names:
            notes.append(f"Not found in any source, check before sending: {', '.join(names)}")
        if route.confidence == "guessed":
            notes.append(f"{route.address} is a guess from the company's address pattern.")
        review = DraftReview(
            address=route.address,
            address_confidence=route.confidence,
            hook_used=attempt.draft.hook_used,
            angle_used=attempt.draft.angle_used,
            reader_calibration=_calibration(style),
            naturalized=not outcome.reverted,
            changes=outcome.changes,
            problems=[p.detail for p in checker.check(subject, outcome.text)],
            review_notes=notes,
        )
        return WrittenLetter(
            recipient=contact.name,
            channel=route.channel,
            subject=subject,
            text=outcome.text,
            review=review,
        )

    async def _profile(self, addressee: Addressee, company: str) -> ReaderStyle | None:
        contact = addressee.contact
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

    async def _draft(
        self, brief: LetterBrief, frame: LetterFrame, checker: LetterChecker
    ) -> _Attempt:
        attempt: _Attempt | None = None
        for _ in range(MAX_DRAFT_ATTEMPTS):
            draft = await self._steps.run("creative", "expert-copywriter", brief, LetterDraft)
            text = frame.assemble(draft.body)
            attempt = _Attempt(draft=draft, text=text, problems=checker.check(draft.subject, text))
            if not attempt.problems:
                return attempt
            problems = [p.detail for p in attempt.problems]
            logger.info("copywriter retry for %s: %s", brief.recipient.name, problems)
            retry = RetryFeedback(previous_attempt=text, problems=problems)
            brief = brief.model_copy(update={"retry": retry})
        assert attempt is not None
        return attempt


def _calibration(style: ReaderStyle | None) -> str:
    if style is None:
        return "neutral: the profiler failed"
    if style.social_style is None:
        return f"role priorities: {', '.join(style.role_priorities) or 'none'}"
    return f"{style.social_style} ({style.confidence} confidence)"
