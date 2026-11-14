"""Build a writer's voice profile per channel: measured habits, an agent's read, user notes."""

import logging

from pydantic import BaseModel, Field

from mozg_outreach.naturalize.models import Channel
from mozg_outreach.naturalize.stylometry import StyleAnalyzer, StyleFeatures
from mozg_outreach.pipelines.steps import PipelineSteps

logger = logging.getLogger(__name__)

MIN_SAMPLES = 3


class VoiceProfileInput(BaseModel):
    channel: Channel
    samples: list[str] = Field(min_length=1)
    measured: StyleFeatures


class VoiceNotes(BaseModel):
    tone: str
    habits: list[str]
    vocabulary: list[str]
    avoid: list[str]
    concerns: list[str]


class VoiceProfile(BaseModel):
    channel: Channel
    features: StyleFeatures
    notes: VoiceNotes
    style_prompts: list[str] = Field(description="The writer's own instructions, kept verbatim.")

    def render(self) -> str:
        """The text handed to the naturalizer as `voice_profile`."""
        f = self.features
        lines = [
            f"Channel: {self.channel}",
            f"Measured on {f.samples} messages the writer sent:",
            f"- about {f.words_mean:.0f} words per message",
            f"- sentences average {f.sentence_words_mean:.0f} words "
            f"(spread {f.sentence_words_std:.0f})",
            f"- per 100 words: {f.contractions_per_100:.1f} contractions, "
            f"{f.commas_per_100:.1f} commas, {f.questions_per_100:.1f} questions, "
            f"{f.exclamations_per_100:.1f} exclamations",
            f"- {f.final_period_rate:.0%} of lines end with a period",
        ]
        if f.greetings:
            lines.append(f"- greetings used: {_counts(f.greetings)}")
        if f.sign_offs:
            lines.append(f"- sign-offs used: {_counts(f.sign_offs)}")
        lines += ["", f"Tone: {self.notes.tone}", "", "Habits to keep:"]
        lines += [f"- {h}" for h in self.notes.habits]
        if self.notes.vocabulary:
            quoted = ", ".join(f'"{v}"' for v in self.notes.vocabulary)
            lines.append(f"Own words and phrases: {quoted}")
        if self.notes.avoid:
            lines += ["Don't copy:", *(f"- {a}" for a in self.notes.avoid)]
        if self.style_prompts:
            lines += ["", "The writer's own instructions (these win over everything above):"]
            lines += self.style_prompts
        return "\n".join(lines)


class VoiceProfiler:
    def __init__(self, steps: PipelineSteps) -> None:
        self._steps = steps

    async def build(
        self, channel: Channel, samples: list[str], style_prompts: list[str]
    ) -> VoiceProfile:
        if len(samples) < MIN_SAMPLES:
            got = len(samples)
            raise ValueError(
                f"a {channel} voice profile needs at least {MIN_SAMPLES} samples, got {got}"
            )
        measured = StyleAnalyzer().features(samples)
        payload = VoiceProfileInput(channel=channel, samples=samples, measured=measured)
        notes = await self._steps.run("creative", "voice-profiler", payload, VoiceNotes)
        logger.info("built %s voice profile from %d samples", channel, len(samples))
        return VoiceProfile(
            channel=channel, features=measured, notes=notes, style_prompts=style_prompts
        )


def _counts(counter: dict[str, int]) -> str:
    return ", ".join(f"{k} ({v})" for k, v in sorted(counter.items(), key=lambda kv: -kv[1]))
