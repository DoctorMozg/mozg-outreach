"""Write one naturalized cover letter for a shortlisted listing."""

import logging

from pydantic import BaseModel, Field

from mozg_outreach.letters.composer import Composition, Delivery, LetterComposer, WrittenLetter
from mozg_outreach.letters.frame import LetterFrame, LetterParty
from mozg_outreach.letters.language import PostingLanguage
from mozg_outreach.letters.models import (
    Angle,
    Hook,
    LetterBrief,
    LetterRecipient,
    Revision,
    SenderSettings,
    Skipped,
)
from mozg_outreach.naturalize.naturalizer import WORD_BUDGETS, Naturalizer, VoiceContext
from mozg_outreach.pipelines.seeker.models import JobContacts, Listing, ListingJudgment
from mozg_outreach.pipelines.seeker.skills import SkillMatch
from mozg_outreach.pipelines.steps import PipelineSteps

logger = logging.getLogger(__name__)

HIRING_TEAM = "Hiring Team"
LETTER_CHARS = 2000
SHARED_INBOX = "shared_inbox"
# The word that opens an application email's subject, by the letter's language.
APPLICATION = {
    "en": "Application",
    "de": "Bewerbung",
    "fr": "Candidature",
    "es": "Candidatura",
    "pt": "Candidatura",
    "it": "Candidatura",
    "nl": "Sollicitatie",
    "ru": "Отклик на вакансию",
}


def application_subject(title: str, language: str, sender: str) -> str:
    """Recruiters file by role and name, so the subject carries both and nothing else."""
    word = APPLICATION.get(language, APPLICATION["en"])
    return f"{word}: {title.strip()} - {sender}".strip(" -")


class CoverLetterRequest(BaseModel):
    listing: Listing
    judgment: ListingJudgment | None
    contacts: JobContacts | None
    skills: SkillMatch
    background: str = Field(
        min_length=1, description="The CV and brief facts; every claim about the sender is in it."
    )
    sender: SenderSettings
    voice: VoiceContext = Field(default_factory=lambda: VoiceContext(profile=None, samples=[]))
    revision: Revision | None = None


class JobLetterWriter:
    def __init__(self, steps: PipelineSteps, naturalizer: Naturalizer) -> None:
        self._composer = LetterComposer(steps, naturalizer)

    async def write(self, request: CoverLetterRequest) -> WrittenLetter | Skipped:
        listing = request.listing
        named = request.contacts.named_contact if request.contacts else None
        language = PostingLanguage.of(f"{listing.title}\n{listing.summary}")
        recipient = LetterRecipient(
            name=named.name if named else HIRING_TEAM,
            title=(named.title or "") if named else "",
            company=listing.company,
            relevance="Screens applications for this role.",
        )
        brief = LetterBrief(
            channel="cover_letter",
            language=language.letter,
            recipient=recipient,
            generic_inbox=False,
            word_budget=WORD_BUDGETS["cover_letter"],
            subject_max_chars=None,
            angles=[self._angle(listing, request.skills)],
            hooks=self._hooks(listing, request.judgment),
            verified_entities=[
                listing.company,
                listing.title,
                *(h.skill for h in [*request.skills.must_haves, *request.skills.or_groups]),
                *(h.skill for h in request.skills.nice_to_haves),
            ],
            reader_style=None,
            sender_voice=request.sender.voice,
            sender_background=request.background,
            revision=request.revision,
        )
        party = LetterParty(recipient=named.name if named else None, sender=request.sender.name)
        delivery = _delivery(listing, request.contacts)
        brief.generic_inbox = delivery.confidence == SHARED_INBOX
        logger.info("cover letter for %s to %s", listing.key, recipient.name)
        letter = await self._composer.compose(
            Composition(
                brief=brief,
                frame=LetterFrame.build(
                    "cover_letter", request.sender.tone, party, language.letter
                ),
                voice=request.voice,
                delivery=delivery,
                calibration="cover letter: neutral shape",
                notes=language.notes(),
            )
        )
        if isinstance(letter, WrittenLetter) and "@" in delivery.address and not letter.subject:
            subject = application_subject(listing.title, language.letter, request.sender.name)
            letter = letter.model_copy(update={"subject": subject})
        return letter

    @staticmethod
    def _angle(listing: Listing, skills: SkillMatch) -> Angle:
        matched = [h.skill for h in [*skills.must_haves, *skills.or_groups, *skills.nice_to_haves]]
        need = listing.excerpt(matched, LETTER_CHARS)
        return Angle(
            name="role fit",
            pain_point=f"{listing.company} needs a {listing.title}: {need}",
            value_prop=f"The CV shows hands-on work with {', '.join(matched) or 'the role stack'}.",
        )

    @staticmethod
    def _hooks(listing: Listing, judgment: ListingJudgment | None) -> list[Hook]:
        hooks = [
            Hook(signal=listing.excerpt([], LETTER_CHARS), source="listing", source_url=listing.url)
        ]
        if judgment and judgment.counterparty_evidence:
            hooks.append(
                Hook(
                    signal=judgment.counterparty_evidence, source="listing", source_url=listing.url
                )
            )
        return hooks


def _delivery(listing: Listing, contacts: JobContacts | None) -> Delivery:
    """The person, then HR, then the company inbox, then a guessed careers@, then the form.

    A guessed address only reaches Gmail after the user confirms it, a few a day."""
    named = contacts.named_contact if contacts else None
    if named and named.email:
        return Delivery(address=named.email, confidence="published")
    if contacts and contacts.careers_email:
        return Delivery(address=contacts.careers_email, confidence="published")
    if contacts and contacts.company_email:
        return Delivery(
            address=contacts.company_email,
            confidence=SHARED_INBOX,
            note="A general inbox: the letter asks whoever reads it to pass it to hiring.",
        )
    if contacts and contacts.pattern_guesses:
        return Delivery(
            address=contacts.pattern_guesses[0],
            confidence="guessed",
            note="Guessed from the company's domain and may bounce; Gmail asks you to confirm.",
        )
    apply_url = (contacts.apply_url if contacts else None) or listing.url
    return Delivery(
        address=apply_url,
        confidence="apply_form",
        note="Paste this into the application form; no published email was found.",
    )
