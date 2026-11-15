from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

FIELD_ID_PATTERN = r"^[a-z][a-z0-9_]{0,47}$"


class ProjectMode(StrEnum):
    BIZ = "biz"
    SEEKER = "seeker"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_GATE = "awaiting_gate"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ItemKind(StrEnum):
    COMPANY = "company"
    JOB = "job"
    GIG = "gig"


class EntityKind(StrEnum):
    PERSON = "person"
    COMPANY = "company"


class IdentifierKind(StrEnum):
    DOMAIN = "domain"
    EMAIL = "email"
    LINKEDIN = "linkedin"
    TELEGRAM = "telegram"
    # A full name at a known employer; exact, but only as strong as the employer key.
    NAME_AT_COMPANY = "name_at_company"


class SourceKind(StrEnum):
    WEB = "web"
    TELEGRAM = "telegram"
    FEED = "feed"


class FieldType(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    BOOLEAN = "boolean"
    URL = "url"
    EMAIL = "email"
    DATE = "date"
    LIST = "list"


class FieldDefinition(BaseModel):
    """One user-facing report field; `id` stays stable across spec versions."""

    id: str = Field(pattern=FIELD_ID_PATTERN)
    label: str = Field(min_length=1)
    type: FieldType
    description: str
    required: bool = False
    example: str | None = None


class FieldSpec(BaseModel):
    fields: list[FieldDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> "FieldSpec":
        ids = [f.id for f in self.fields]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate field ids: {duplicates}")
        return self

    def by_id(self) -> dict[str, FieldDefinition]:
        return {f.id: f for f in self.fields}


type FieldScalar = str | int | float | bool | list[str] | None


class BriefFact(BaseModel):
    """A fact about the user or their company that the brief's own fields have no place for."""

    label: str = Field(min_length=1)
    value: str = Field(min_length=1)
    source_url: str | None = Field(
        default=None, description="The page it was read on; null when the user or the CV says it."
    )


def facts_text(facts: list[BriefFact]) -> str:
    return "\n".join(f"- {fact.label}: {fact.value}" for fact in facts)


class FieldValue(BaseModel):
    """An extracted value with the evidence it came from."""

    value: FieldScalar
    source_url: str | None = None
    quote: str | None = None
    confidence: Literal["verified", "unverified", "guessed"] = "unverified"
    fetched_at: datetime | None = None


class ItemCandidate(BaseModel):
    """A pipeline's proposed item before it is merged into storage."""

    kind: ItemKind
    dedup_key: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=300)
    fields: dict[str, FieldValue] = Field(default_factory=dict)


class ScoreAxis(BaseModel):
    """One scored dimension; a None value means not assessed yet and is left out of the total."""

    axis: str
    value: float | None = Field(ge=0, le=100)
    weight: float = Field(ge=0)
    reason: str


class ItemScore(BaseModel):
    total: float | None = Field(description="Weighted mean of assessed axes; None if none were.")
    axes: list[ScoreAxis]
    blockers: list[str] = Field(description="Hard disqualifiers that were hit.")
