"""Naturalize one draft: pick exemplars and limits, call the agent, keep only a safe rewrite."""

import logging

from pydantic import BaseModel, Field

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.naturalize.models import (
    Channel,
    NaturalizeInput,
    NaturalizeResult,
    RetryFeedback,
    SoftPunctuation,
    WordBudget,
)
from mozg_outreach.naturalize.punctuation import SOFT_BANS
from mozg_outreach.naturalize.validation import RewriteOutcome, RewriteValidator, word_count
from mozg_outreach.naturalize.voice import VoiceProfile
from mozg_outreach.pipelines.steps import PipelineSteps

logger = logging.getLogger(__name__)

# Body words, excluding salutation and sign-off; from the legacy letter and proposal writers.
WORD_BUDGETS: dict[Channel, WordBudget] = {
    "email": WordBudget(min=120, max=180),
    "dm": WordBudget(min=60, max=100),
    "cover_letter": WordBudget(min=120, max=199),
    "proposal": WordBudget(min=280, max=350),
}
LENGTH_TOLERANCE_PCT = 10
MAX_EXEMPLARS = 4
MAX_ATTEMPTS = 2


class NaturalizeRequest(BaseModel):
    text: str = Field(min_length=1)
    channel: Channel
    preserve: list[str] = Field(default_factory=list)


class VoiceContext(BaseModel):
    profile: VoiceProfile | None
    samples: list[str] = Field(description="The writer's non-held-out samples for this channel.")


class Naturalizer:
    def __init__(self, steps: PipelineSteps) -> None:
        self._steps = steps

    async def naturalize(self, request: NaturalizeRequest, voice: VoiceContext) -> RewriteOutcome:
        payload = NaturalizeInput(
            text=request.text,
            channel=request.channel,
            # Medium fixes structure as well as wording; heavy would cut length and lose facts.
            severity="medium",
            length_tolerance_pct=LENGTH_TOLERANCE_PCT,
            word_budget=WORD_BUDGETS[request.channel],
            preserve=request.preserve,
            allowed_punctuation=habitual_punctuation(voice.samples),
            voice_profile=voice.profile.render() if voice.profile else None,
            exemplars=pick_exemplars(voice.samples, request.text),
        )
        validator = RewriteValidator(payload)
        outcome: RewriteOutcome | None = None
        for _ in range(MAX_ATTEMPTS):
            try:
                result = await self._steps.run(
                    "creative", "expert-naturalizer", payload, NaturalizeResult
                )
            except (AgentRunError, AgentOutputError) as exc:
                # The draft is still usable as written; the user sees why it wasn't rewritten.
                logger.warning("naturalizer failed for a %s draft: %s", request.channel, exc)
                return validator.keep_original(f"Naturalizer failed, text kept as written: {exc}")
            outcome = validator.accept(result)
            if not outcome.reverted:
                return outcome
            # A near-miss usually needs a small fix, which beats sending the unedited draft.
            problems = [p.detail for p in outcome.damage]
            logger.info("naturalizer retry for a %s draft: %s", request.channel, problems)
            feedback = RetryFeedback(previous_attempt=result.text, problems=problems)
            payload = payload.model_copy(update={"retry": feedback})
        assert outcome is not None
        return outcome


def pick_exemplars(samples: list[str], text: str) -> list[str]:
    """Samples closest in length to the draft, since length shapes rhythm more than topic."""
    target = word_count(text)
    return sorted(samples, key=lambda s: abs(word_count(s) - target))[:MAX_EXEMPLARS]


def habitual_punctuation(samples: list[str]) -> list[SoftPunctuation]:
    """A soft-banned mark is the writer's own when it shows up in at least half their samples."""
    if len(samples) < 2:
        return []
    return [
        name
        for name, rx in SOFT_BANS.items()
        if sum(1 for s in samples if rx.search(s)) * 2 >= len(samples)
    ]
