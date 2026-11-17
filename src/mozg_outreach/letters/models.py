"""Contracts between letter pipelines and the reader profiler and copywriter agents."""

from typing import Literal

from pydantic import BaseModel, Field

from mozg_outreach.naturalize.models import Channel, RetryFeedback, WordBudget

# Sets the salutation and sign-off; the voice profile shapes everything in between.
Tone = Literal["warm", "neutral", "formal"]
SocialStyle = Literal["driver", "analytical", "amiable", "expressive"]
StyleConfidence = Literal["high", "medium", "low", "none"]
TouchPurpose = Literal["open", "follow_up", "breakup"]
LetterVariant = Literal["observation", "problem", "peer"]

# One way in per candidate, so the judge compares real alternatives, not three near-copies.
VARIANTS: dict[LetterVariant, str] = {
    "observation": "Open on the hook itself: what you noticed about them, stated plainly.",
    "problem": "Open on the problem the hook makes likely, in the reader's own terms.",
    "peer": (
        "Open on what a comparable company did, from sender_background, then tie it to the "
        "hook. With no comparable case in the brief, open on the hook instead."
    ),
}

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
    date: str | None = Field(
        default=None, description="When it happened, YYYY-MM or YYYY-MM-DD; null if unknown."
    )


class LetterRecipient(BaseModel):
    name: str = Field(min_length=1)
    title: str = ""
    company: str
    relevance: str = Field(default="", description="Why this person is the right reader.")


class Revision(BaseModel):
    """The user's change request on an earlier draft of the same letter."""

    previous_text: str = Field(min_length=1, description="That draft, salutation and sign-off too.")
    suggestion: str = Field(min_length=1, description="What the user wants changed, as written.")


class RevisionTarget(BaseModel):
    """Which of a company's letters to rewrite, and how."""

    recipient: str
    channel: Channel
    step: int | None = Field(default=None, description="The approach step; null outside a plan.")
    revision: Revision


class EarlierTouch(BaseModel):
    step: int
    day: int
    channel: str = Field(description="email, linkedin or phone.")
    purpose: TouchPurpose
    talking_points: str
    text: str | None = Field(description="The letter of that touch; null for a call.")


class TouchContext(BaseModel):
    """Where a letter sits in its approach plan, and what the reader got before it."""

    step: int
    total: int = Field(description="Touches in the whole sequence, to every contact.")
    day: int = Field(description="Days after the sequence's first touch.")
    purpose: TouchPurpose
    talking_points: str = Field(description="What the plan says this touch adds.")
    in_thread: bool = Field(
        description="An email replying in the thread of the earlier email; it has no subject."
    )
    earlier: list[EarlierTouch] = Field(description="Earlier touches to this reader, oldest first.")
    others_contacted: list[str] = Field(
        default_factory=list,
        description="People at the company the sender wrote to before this reader's first letter.",
    )


class LetterBrief(BaseModel):
    channel: Channel
    language: str = Field(
        min_length=2, max_length=2, description="ISO 639-1 code; subject and body use it."
    )
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
    ask: str | None = Field(
        default=None, description="The approach plan's ask; the letter makes exactly this ask."
    )
    offers: list[str] = Field(
        default_factory=list,
        description="Things the sender can send on a yes; an ask offers only these.",
    )
    touch: TouchContext | None = Field(default=None, description="Null outside an approach plan.")
    variant: str | None = Field(
        default=None, description="How this draft opens; other drafts of the letter open otherwise."
    )
    avoid_phrasings: list[str] = Field(
        default_factory=list,
        description="Openings and asks of the sender's recent letters to other companies.",
    )
    revision: Revision | None = None
    retry: RetryFeedback | None = None


class LetterDraft(BaseModel):
    subject: str | None = Field(description="Email only; null for other channels.")
    body: str = Field(min_length=1, description="Paragraphs only, no salutation or sign-off.")
    hook_used: str = Field(description="The hook signal the opening sentence rests on.")
    angle_used: str
    concerns: list[str] = Field(description=CONCERNS)


class CandidateReview(BaseModel):
    variant: str
    subject: str | None
    text: str = Field(description="The checked draft, before naturalizing.")
    problems: list[str]
    score: int | None = Field(description="Judge points over both orders; null if not judged.")
    chosen: bool


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
    candidates: list[CandidateReview] = Field(
        default_factory=list, description="Every draft the judge compared; empty for one draft."
    )


class JudgedLetter(BaseModel):
    label: str
    subject: str | None
    body: str


class JudgeInput(BaseModel):
    channel: Channel
    language: str
    recipient: LetterRecipient
    purpose: TouchPurpose = Field(description="open for a reader's first letter.")
    hooks: list[Hook]
    ask: str | None
    sender_voice: str
    candidates: list[JudgedLetter]


class AxisScores(BaseModel):
    """Each axis 1 (poor) to 5 (strong)."""

    hook_relevance: int = Field(ge=1, le=5)
    specificity: int = Field(ge=1, le=5)
    one_clear_ask: int = Field(ge=1, le=5)
    reader_effort: int = Field(ge=1, le=5, description="5 when it takes seconds to read and act.")
    sounds_like_sender: int = Field(ge=1, le=5)
    no_ai_tells: int = Field(ge=1, le=5)

    def total(self) -> int:
        return sum(self.model_dump().values())


class CandidateScore(BaseModel):
    label: str
    scores: AxisScores
    reason: str = Field(description="One sentence: what decided these scores.")


class JudgeResult(BaseModel):
    candidates: list[CandidateScore]
    concerns: list[str] = Field(description=CONCERNS)


class Skipped(BaseModel):
    contact: str
    reason: str


class SenderSettings(BaseModel):
    name: str = Field(min_length=1, description="From the project settings; signs every letter.")
    tone: Tone = "neutral"
    voice: str = Field(default="", description="The strategy's sender voice description.")
    card: str | None = Field(default=None, description="Signature card under every email.")
