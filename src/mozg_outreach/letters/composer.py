"""Draft one letter, check it, naturalize it, and say what still needs the user's eye."""

import logging

from pydantic import BaseModel

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.letters.checks import LetterChecker, LetterProblem
from mozg_outreach.letters.frame import LetterFrame
from mozg_outreach.letters.models import DraftReview, LetterBrief, LetterDraft, Skipped
from mozg_outreach.naturalize.models import Channel, RetryFeedback
from mozg_outreach.naturalize.naturalizer import Naturalizer, NaturalizeRequest, VoiceContext
from mozg_outreach.pipelines.steps import PipelineSteps

logger = logging.getLogger(__name__)

MAX_DRAFT_ATTEMPTS = 2


class Delivery(BaseModel):
    """Where the user sends the letter by hand, and how sure we are of that address."""

    address: str
    confidence: str
    note: str | None = None


class Composition(BaseModel):
    brief: LetterBrief
    frame: LetterFrame
    voice: VoiceContext
    delivery: Delivery
    calibration: str = "neutral"


class WrittenLetter(BaseModel):
    recipient: str
    channel: Channel
    subject: str | None
    text: str
    review: DraftReview


class _Attempt(BaseModel):
    draft: LetterDraft
    text: str
    problems: list[LetterProblem]


class LetterComposer:
    def __init__(self, steps: PipelineSteps, naturalizer: Naturalizer) -> None:
        self._steps = steps
        self._naturalizer = naturalizer

    async def compose(self, job: Composition) -> WrittenLetter | Skipped:
        brief, recipient = job.brief, job.brief.recipient.name
        checker = LetterChecker(brief, job.frame)
        try:
            attempt = await self._draft(brief, job.frame, checker)
        except (AgentRunError, AgentOutputError) as exc:
            logger.warning("copywriter failed for %s: %s", recipient, exc)
            return Skipped(contact=recipient, reason=f"copywriter failed: {exc}")
        preserve = [recipient, brief.recipient.company]
        preserve += [e for e in brief.verified_entities if e and e in attempt.text]
        outcome = await self._naturalizer.naturalize(
            NaturalizeRequest(text=attempt.text, channel=brief.channel, preserve=preserve),
            job.voice,
        )
        subject = attempt.draft.subject
        notes = [*attempt.draft.concerns, *outcome.review_notes]
        if outcome.reverted and outcome.damage:
            damage = "; ".join(p.detail for p in outcome.damage)
            notes.append(f"Naturalized version rejected, original kept: {damage}")
        names = checker.unverified_names(outcome.text)
        if names:
            notes.append(f"Not found in any source, check before sending: {', '.join(names)}")
        if job.delivery.note:
            notes.append(job.delivery.note)
        review = DraftReview(
            address=job.delivery.address,
            address_confidence=job.delivery.confidence,
            hook_used=attempt.draft.hook_used,
            angle_used=attempt.draft.angle_used,
            reader_calibration=job.calibration,
            naturalized=not outcome.reverted,
            before_naturalize=attempt.text,
            changes=outcome.changes,
            problems=[p.detail for p in checker.check(subject, outcome.text)],
            review_notes=notes,
        )
        return WrittenLetter(
            recipient=recipient,
            channel=brief.channel,
            subject=subject,
            text=outcome.text,
            review=review,
        )

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
