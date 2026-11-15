"""Write one naturalized proposal for a shortlisted gig."""

import logging
import re

from pydantic import BaseModel, Field

from mozg_outreach.letters.composer import Composition, Delivery, LetterComposer, WrittenLetter
from mozg_outreach.letters.frame import LetterFrame, LetterParty
from mozg_outreach.letters.language import PostingLanguage
from mozg_outreach.letters.models import (
    Angle,
    Hook,
    LetterBrief,
    LetterRecipient,
    SenderSettings,
    Skipped,
)
from mozg_outreach.naturalize.naturalizer import WORD_BUDGETS, Naturalizer, VoiceContext
from mozg_outreach.pipelines.seeker.models import Listing, Money
from mozg_outreach.pipelines.seeker.skills import SkillMatch
from mozg_outreach.pipelines.steps import PipelineSteps

logger = logging.getLogger(__name__)

MAX_SCOPE_HOOKS = 4
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
# Gigs name a company or handle, never a person, so proposals open with "Hi there,".
UNNAMED_CLIENT = "there"


class ProposalRequest(BaseModel):
    gig: Listing
    skills: SkillMatch
    rate: Money | None = Field(description="The user's floor; the only rate a proposal may quote.")
    background: str = Field(
        min_length=1, description="The CV and brief facts; every claim about the sender is in it."
    )
    sender: SenderSettings
    voice: VoiceContext = Field(default_factory=lambda: VoiceContext(profile=None, samples=[]))


class ProposalWriter:
    def __init__(self, steps: PipelineSteps, naturalizer: Naturalizer) -> None:
        self._composer = LetterComposer(steps, naturalizer)

    async def write(self, request: ProposalRequest) -> WrittenLetter | Skipped:
        listing = request.gig
        recipient = LetterRecipient(
            name=UNNAMED_CLIENT,
            company=listing.company,
            relevance="Posted the gig.",
        )
        language = PostingLanguage.of(f"{listing.title}\n{listing.summary}")
        skills = request.skills
        matched = [h.skill for h in [*skills.must_haves, *skills.or_groups, *skills.nice_to_haves]]
        brief = LetterBrief(
            channel="proposal",
            language=language.letter,
            recipient=recipient,
            generic_inbox=False,
            word_budget=WORD_BUDGETS["proposal"],
            subject_max_chars=None,
            angles=[
                Angle(
                    name="gig fit",
                    pain_point=f"{listing.title}: {listing.summary[:300]}",
                    value_prop=f"The CV shows work with {', '.join(matched) or 'this stack'}.",
                )
            ],
            hooks=scope_hooks(request.gig),
            verified_entities=[listing.company, listing.title, *matched, *pricing(request)],
            reader_style=None,
            sender_voice=request.sender.voice,
            sender_background=request.background,
        )
        party = LetterParty(recipient=None, sender=request.sender.name)
        logger.info("proposal for %s", listing.key)
        return await self._composer.compose(
            Composition(
                brief=brief,
                frame=LetterFrame.build("proposal", request.sender.tone, party, language.letter),
                voice=request.voice,
                delivery=Delivery(
                    address=listing.url,
                    confidence="apply_form",
                    note=_application_method(listing) or "Submit it on the gig's page.",
                ),
                calibration="proposal: neutral shape",
                notes=language.notes(),
            )
        )


def scope_hooks(gig: Listing) -> list[Hook]:
    """The gig's own sentences, so milestones can quote the scope instead of paraphrasing it."""
    sentences = [s.strip() for s in _SENTENCE.split(gig.summary) if len(s.strip()) > 20]
    picked = sentences[:MAX_SCOPE_HOOKS] or [gig.summary or gig.title]
    return [Hook(signal=s, source="gig", source_url=gig.url) for s in picked]


def _application_method(gig: Listing) -> str | None:
    return gig.terms.application_method if gig.terms else None


def pricing(request: ProposalRequest) -> list[str]:
    lines = []
    budget = request.gig.pay
    if budget:
        lines.append(f"Posted budget: {budget.raw}")
    if request.rate:
        rate = request.rate
        lines.append(f"Rate: {rate.currency} {rate.amount:,.0f} per {rate.period}")
    return lines
