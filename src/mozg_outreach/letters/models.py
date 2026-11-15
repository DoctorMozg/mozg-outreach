"""Contracts between letter pipelines and the reader profiler and copywriter agents."""

from typing import Literal

from pydantic import BaseModel, Field

from mozg_outreach.naturalize.models import Channel, RetryFeedback, WordBudget

# Sets the salutation and sign-off; the voice profile shapes everything in between.
Tone = Literal["warm", "neutral", "formal"]
SocialStyle = Literal["driver", "analytical", "amiable", "expressive"]
StyleConfidence = Literal["high", "medium", "low", "none"]

CONCERNS = "Anything the caller should know: conflicting or unverified sources, thin evidence."


class ProfilerInput(BaseModel):
    name: str = Field(min_length=1)
    title: str
    company: str
    linkedin: str | None
    seed_signals: list[str] = Field(description="Public activity already found for this person.")
    recency_months: int = Field(default=12, ge=1)


class StyleCue(BaseModel):
    cue: str = Field(description="An observable trait of how they write or speak in public.")
    source_url: str


class ReaderStyle(BaseModel):
    social_style: SocialStyle | None = Field(description="Null unless a cited cue supports it.")
    role_priorities: list[str] = Field(description="What the role tends to care about.")
    confidence: StyleConfidence
    calibration_directive: list[str] = Field(
        max_length=4, description="Imperative lines on structure, length and which angle leads."
    )
    evidence: list[StyleCue]
    concerns: list[str] = Field(description=CONCERNS)


class Angle(BaseModel):
    name: str
    pain_point: str
    value_prop: str


class Hook(BaseModel):
    """A fact about the recipient or their company that a letter may open with."""

    signal: str = Field(min_length=1)
    source: str = Field(description="Where it came from, e.g. news, tech, scout, research.")
    source_url: str | None = None


class LetterRecipient(BaseModel):
    name: str = Field(min_length=1)
    title: str = ""
    company: str
    relevance: str = Field(default="", description="Why this person is the right reader.")


class LetterBrief(BaseModel):
    channel: Channel
    recipient: LetterRecipient
    generic_inbox: bool = Field(
        description="The address is a shared inbox, so the body must name the recipient."
    )
    word_budget: WordBudget = Field(description="Body words, without salutation and sign-off.")
    subject_max_chars: int | None = Field(description="Null when the channel has no subject.")
    angles: list[Angle] = Field(min_length=1)
    hooks: list[Hook] = Field(min_length=1)
    verified_entities: list[str] = Field(
        description="The only names, products, numbers and dates the letter may mention."
    )
    reader_style: ReaderStyle | None
    sender_voice: str
    sender_background: str | None = Field(
        default=None,
        description="The sender's CV or company facts; every claim about the sender comes from it.",
    )
    retry: RetryFeedback | None = None


class LetterDraft(BaseModel):
    subject: str | None = Field(description="Email only; null for other channels.")
    body: str = Field(min_length=1, description="Paragraphs only, no salutation or sign-off.")
    hook_used: str = Field(description="The hook signal the opening sentence rests on.")
    angle_used: str
    concerns: list[str] = Field(description=CONCERNS)


class DraftReview(BaseModel):
    """What the user needs beside the letter to judge it before sending it by hand."""

    address: str = Field(description="Email address or LinkedIn URL to send it to by hand.")
    address_confidence: str = Field(description="published, guessed, linkedin or shared_inbox.")
    hook_used: str
    angle_used: str
    reader_calibration: str = Field(description="Style read used, or the role-priority fallback.")
    naturalized: bool
    before_naturalize: str = Field(description="The checked draft the naturalizer started from.")
    changes: list[str]
    problems: list[str] = Field(description="Format or evidence rules the final text still breaks.")
    review_notes: list[str] = Field(description="Facts to check: unverified names, vague claims.")


class Skipped(BaseModel):
    contact: str
    reason: str


class SenderSettings(BaseModel):
    name: str = Field(min_length=1, description="From the project settings; signs every letter.")
    tone: Tone = "neutral"
    voice: str = Field(default="", description="The strategy's sender voice description.")
