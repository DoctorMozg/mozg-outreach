import hashlib
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

type ItemKind = Literal["job", "gig", "company", "signal"]


def normalize_text(text: str) -> str:
    return " ".join(text.split())


def hash_text(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode()).hexdigest()


def _as_utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class SourceItem(BaseModel):
    """Normalized listing from any source. Dedup key is (source, source_item_id)."""

    source: str
    source_item_id: str
    url: str | None = None
    kind: ItemKind
    title: str | None = None
    raw_text: str
    raw_payload: dict[str, object]
    published_at: datetime | None = None
    fetched_at: datetime
    author: str | None = None
    org_name: str | None = None
    location: str | None = None
    tags: list[str] = Field(default_factory=list)
    license_note: str | None = None
    content_hash: str = ""

    @field_validator("published_at", "fetched_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        # Some feeds send naive timestamps; they are UTC.
        return _as_utc(value)

    @model_validator(mode="after")
    def _fill_hash(self) -> "SourceItem":
        if not self.content_hash:
            self.content_hash = hash_text(self.raw_text)
        return self

    @property
    def dedup_key(self) -> tuple[str, str]:
        return (self.source, self.source_item_id)


class SourceQuery(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    since: datetime | None = None
    limit: int = Field(default=100, ge=1)
    total_limit: int | None = Field(default=None, ge=1, description="Cap across all channels.")


class FetchResult(BaseModel):
    items: list[SourceItem] = Field(default_factory=list)
    cursor: str | None = None
    errors: list[str] = Field(default_factory=list)
