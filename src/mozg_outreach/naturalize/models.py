"""Contract between the service and the naturalizer agent."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Channel = Literal["email", "dm", "cover_letter", "proposal"]
Severity = Literal["light", "medium", "heavy"]
# Only these can be lifted by a voice profile; non-ASCII punctuation is banned in every draft.
SoftPunctuation = Literal["semicolon", "spaced_hyphen", "double_hyphen", "three_dots"]


class WordBudget(BaseModel):
    min: int = Field(ge=1)
    max: int = Field(ge=1)

    @model_validator(mode="after")
    def _ordered(self) -> "WordBudget":
        if self.min > self.max:
            raise ValueError(f"word budget min {self.min} > max {self.max}")
        return self


class RetryFeedback(BaseModel):
    previous_attempt: str
    problems: list[str] = Field(min_length=1, description="Limits the previous attempt broke.")


class NaturalizeInput(BaseModel):
    text: str = Field(min_length=1)
    channel: Channel
    severity: Severity = Field(description="Set by the service; short texts are always light.")
    length_tolerance_pct: int = Field(ge=0, le=50, description="Allowed word-count change.")
    word_budget: WordBudget | None
    preserve: list[str] = Field(description="Terms to keep verbatim: names, numbers, the ask.")
    allowed_punctuation: list[SoftPunctuation]
    voice_profile: str | None = Field(description="The writer's voice notes for this channel.")
    exemplars: list[str] = Field(description="Messages the writer wrote themselves, same genre.")
    retry: RetryFeedback | None = Field(
        default=None, description="Set when the previous rewrite broke a limit; fix only that."
    )


class NaturalizeResult(BaseModel):
    text: str = Field(min_length=1)
    changes: list[str] = Field(description="One short line per kind of change made.")
    specificity_needed: list[str] = Field(
        description="Vague claims that need a real fact from the writer; never invent one."
    )
    concerns: list[str]
