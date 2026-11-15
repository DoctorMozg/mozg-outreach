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
    LetterBrief,
    LetterRecipient,
    ProfilerInput,
    ReaderStyle,
    RevisionTarget,
    SenderSettings,
    Skipped,
)
from mozg_outreach.naturalize.models import Channel
from mozg_outreach.naturalize.naturalizer import WORD_BUDGETS, Naturalizer, VoiceContext
from mozg_outreach.pipelines.biz.enrichment_models import CompanyDossier
from mozg_outreach.pipelines.biz.letter_briefs import (
    Addressee,
    CompanyEvidence,
    LetterBriefBuilder,
)
from mozg_outreach.pipelines.steps import PipelineSteps

logger = logging.getLogger(__name__)

# Company letters stay in English until a brief names its market's language.
BIZ_LANGUAGE = "en"

SUBJECT_MAX_CHARS = 55


class LetterRequest(BaseModel):
    company: str = Field(min_length=1)
    fields: dict[str, FieldValue]
    dossier: CompanyDossier
    angles: list[Angle] = Field(min_length=1)
    sender: SenderSettings
    background: str | None = Field(default=None, description="Facts about the sender's company.")
    voices: dict[Channel, VoiceContext] = Field(default_factory=dict)
    revision: RevisionTarget | None = Field(default=None, description="Rewrite only this letter.")


class LetterBatch(BaseModel):
    letters: list[WrittenLetter]
    skipped: list[Skipped]


class BizLetterWriter:
    def __init__(self, steps: PipelineSteps, naturalizer: Naturalizer) -> None:
        self._steps = steps
        self._composer = LetterComposer(steps, naturalizer)

    async def write(self, request: LetterRequest) -> LetterBatch:
        builder = LetterBriefBuilder(request.company, request.dossier)
        evidence = builder.evidence(request.fields)
        recipients = builder.recipients()
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
            language=BIZ_LANGUAGE,
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
            sender_background=request.background,
            revision=request.revision.revision if request.revision else None,
        )
        party = LetterParty(recipient=contact.name, sender=request.sender.name)
        guessed = route.confidence == "guessed"
        return await self._composer.compose(
            Composition(
                brief=brief,
                frame=LetterFrame.build(route.channel, request.sender.tone, party, BIZ_LANGUAGE),
                voice=request.voices.get(route.channel, VoiceContext(profile=None, samples=[])),
                delivery=Delivery(
                    address=route.address,
                    confidence=route.confidence,
                    note=f"{route.address} is a guess from the company's address pattern."
                    if guessed
                    else None,
                ),
                calibration=_calibration(style),
            )
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


def _calibration(style: ReaderStyle | None) -> str:
    if style is None:
        return "neutral: the profiler failed"
    if style.social_style is None:
        return f"role priorities: {', '.join(style.role_priorities) or 'none'}"
    return f"{style.social_style} ({style.confidence} confidence)"
