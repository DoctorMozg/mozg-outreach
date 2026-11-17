"""Draft one letter, check it, naturalize it, and say what still needs the user's eye."""

import asyncio
import logging

from pydantic import BaseModel, Field

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.letters.checks import LetterChecker, LetterProblem
from mozg_outreach.letters.frame import LetterFrame
from mozg_outreach.letters.judge import LetterJudge
from mozg_outreach.letters.models import (
    VARIANTS,
    CandidateReview,
    DraftReview,
    LetterBrief,
    LetterDraft,
    LetterVariant,
    Skipped,
)
from mozg_outreach.letters.sameness import Phrasing, echoes
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
    notes: list[str] = Field(default_factory=list, description="Shown with the review notes.")
    recent: list[Phrasing] = Field(
        default_factory=list, description="Recent letters to other companies, to not echo."
    )
    candidates: int = Field(
        default=1, ge=1, le=len(VARIANTS), description="Drafts to write; a judge picks one."
    )


class WrittenLetter(BaseModel):
    recipient: str
    channel: Channel
    step: int | None = None
    subject: str | None
    text: str
    review: DraftReview


class _Attempt(BaseModel):
    variant: str = "single"
    draft: LetterDraft
    text: str
    problems: list[LetterProblem]


class _Choice(BaseModel):
    attempts: list[_Attempt]
    winner: int
    scores: dict[int, int] = Field(default_factory=dict, description="Judge points by attempt.")
    notes: list[str] = Field(default_factory=list)

    def reviews(self) -> list[CandidateReview]:
        if len(self.attempts) < 2:
            return []
        return [
            CandidateReview(
                variant=a.variant,
                subject=a.draft.subject,
                text=a.text,
                problems=[p.detail for p in a.problems],
                score=self.scores.get(i),
                chosen=i == self.winner,
            )
            for i, a in enumerate(self.attempts)
        ]


class LetterComposer:
    def __init__(self, steps: PipelineSteps, naturalizer: Naturalizer) -> None:
        self._steps = steps
        self._naturalizer = naturalizer
        self._judge = LetterJudge(steps)

    async def compose(self, job: Composition) -> WrittenLetter | Skipped:
        brief, recipient = job.brief, job.brief.recipient.name
        checker = LetterChecker(brief, job.frame)
        try:
            choice = await self._choose(job, checker)
        except (AgentRunError, AgentOutputError) as exc:
            logger.warning("copywriter failed for %s: %s", recipient, exc)
            return Skipped(contact=recipient, reason=f"copywriter failed: {exc}")
        attempt = choice.attempts[choice.winner]
        preserve = [recipient, brief.recipient.company]
        preserve += [e for e in brief.verified_entities if e and e in attempt.text]
        outcome = await self._naturalizer.naturalize(
            NaturalizeRequest(
                text=attempt.text,
                channel=brief.channel,
                language=brief.language,
                word_budget=brief.word_budget,
                preserve=preserve,
            ),
            job.voice,
        )
        limit = brief.max_chars
        if limit is not None and len(outcome.text) > limit >= len(attempt.text):
            # The limit is the platform's, so a longer rewrite can't be sent at all.
            note = f"Naturalized version ran past {limit} characters, original kept."
            outcome = outcome.model_copy(
                update={
                    "text": attempt.text,
                    "reverted": True,
                    "changes": [],
                    "review_notes": [*outcome.review_notes, note],
                }
            )
        subject = attempt.draft.subject
        notes = [*job.notes, *choice.notes, *attempt.draft.concerns, *outcome.review_notes]
        if outcome.reverted and outcome.damage:
            damage = "; ".join(p.detail for p in outcome.damage)
            notes.append(f"Naturalized version rejected, original kept: {damage}")
        notes += checker.warnings(outcome.text)
        notes += echoes(job.recent, outcome.text)
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
            candidates=choice.reviews(),
        )
        return WrittenLetter(
            recipient=recipient,
            channel=brief.channel,
            step=brief.touch.step if brief.touch else None,
            subject=subject,
            text=job.frame.finish(outcome.text),
            review=review,
        )

    async def _choose(self, job: Composition, checker: LetterChecker) -> _Choice:
        """Drafts every candidate; the fewest hard problems go to the judge when it's a tie."""
        if job.candidates == 1:
            return _Choice(attempts=[await self._draft(job.brief, job.frame, checker)], winner=0)
        variants = list(VARIANTS)[: job.candidates]
        tried = await asyncio.gather(
            *(self._candidate(job, checker, v) for v in variants), return_exceptions=True
        )
        attempts = [t for t in tried if isinstance(t, _Attempt)]
        failed = [t for t in tried if not isinstance(t, _Attempt)]
        for error in failed:
            if not isinstance(error, (AgentRunError, AgentOutputError)):
                raise error
        if not attempts:
            raise failed[0]
        notes = [f"{len(failed)} of {len(variants)} drafts failed: {failed[0]}"] if failed else []
        fewest = min(len(a.problems) for a in attempts)
        finalists = [i for i, a in enumerate(attempts) if len(a.problems) == fewest]
        if len(finalists) == 1:
            return _Choice(attempts=attempts, winner=finalists[0], notes=notes)
        try:
            pick = await self._judge.pick(job.brief, [attempts[i].draft for i in finalists])
        except (AgentRunError, AgentOutputError) as exc:
            logger.warning("letter judge failed for %s: %s", job.brief.recipient.name, exc)
            notes.append(f"The judge failed, so the first clean draft was kept: {exc}")
            return _Choice(attempts=attempts, winner=finalists[0], notes=notes)
        scores = {finalists[i]: score for i, score in enumerate(pick.scores)}
        return _Choice(attempts=attempts, winner=finalists[pick.winner], scores=scores, notes=notes)

    async def _candidate(
        self, job: Composition, checker: LetterChecker, variant: LetterVariant
    ) -> _Attempt:
        brief = job.brief.model_copy(update={"variant": VARIANTS[variant]})
        attempt = await self._draft(brief, job.frame, checker)
        return attempt.model_copy(update={"variant": variant})

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
