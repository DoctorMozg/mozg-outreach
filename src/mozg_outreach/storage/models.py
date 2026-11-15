import logging
from datetime import UTC, date, datetime

from sqlalchemy import JSON, ForeignKey, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from mozg_outreach.domain import (
    EntityKind,
    IdentifierKind,
    ItemKind,
    ProjectMode,
    RunStatus,
    SourceKind,
)

logger = logging.getLogger(__name__)

type JsonObject = dict[str, object]

# Key in `Item.details` holding the ISO time of the last enrichment.
ENRICHED_AT = "enriched_at"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {JsonObject: JSON}  # noqa: RUF012


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    mode: Mapped[ProjectMode]
    sender_name: Mapped[str | None] = mapped_column(String(100))
    # warm, neutral or formal: picks the letter salutation and sign-off.
    sender_tone: Mapped[str] = mapped_column(String(10), default="neutral")
    brief: Mapped[JsonObject | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    field_specs: Mapped[list["FieldSpecVersion"]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="FieldSpecVersion.version"
    )


class FieldSpecVersion(Base):
    """Versioned report schema. Accepted versions are immutable; edits create a new version."""

    __tablename__ = "field_spec_versions"
    __table_args__ = (UniqueConstraint("project_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    version: Mapped[int]
    spec: Mapped[JsonObject]
    accepted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    project: Mapped[Project] = relationship(back_populates="field_specs")


class SourceConfig(Base):
    __tablename__ = "source_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(40))
    config: Mapped[JsonObject] = mapped_column(default=dict)
    enabled: Mapped[bool] = mapped_column(default=True)
    # Where the last fetch stopped, so the next run reads only what is new.
    cursor: Mapped[str | None] = mapped_column(Text)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    pipeline: Mapped[str] = mapped_column(String(60))
    status: Mapped[RunStatus] = mapped_column(default=RunStatus.PENDING)
    phase: Mapped[str | None] = mapped_column(String(60))
    pending_gate: Mapped[JsonObject | None]
    # The flow's own state, saved after every phase so a run resumes where it stopped.
    state: Mapped[JsonObject] = mapped_column(default=dict)
    field_spec_version: Mapped[int | None]
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]


class RunEvent(Base):
    """Append-only run log; `seq` orders events per run and backs SSE replay."""

    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    seq: Mapped[int]
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[JsonObject] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Item(Base):
    """One discovered opportunity; `dedup_key` makes reruns update rather than duplicate."""

    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("project_id", "dedup_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    last_run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    kind: Mapped[ItemKind]
    dedup_key: Mapped[str] = mapped_column(String(300))
    title: Mapped[str] = mapped_column(String(300))
    fields: Mapped[JsonObject] = mapped_column(default=dict)
    raw: Mapped[JsonObject] = mapped_column(default=dict)
    score_total: Mapped[float | None] = mapped_column(index=True)
    score: Mapped[JsonObject | None]
    # Pipeline-specific sections (fit verdict, dossier), keyed by name and stored as JSON.
    details: Mapped[JsonObject] = mapped_column(default=dict)
    selected: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    @property
    def enriched(self) -> bool:
        return "dossier" in self.details

    @property
    def enriched_at(self) -> datetime | None:
        """When the dossier was last built; None for no dossier or one stored before timestamps."""
        stamp = self.details.get(ENRICHED_AT)
        return datetime.fromisoformat(stamp) if isinstance(stamp, str) else None

    @property
    def dated(self) -> date:
        """When the lead was posted; without a posting date, the day a run first found it."""
        posted = self.fields.get("posted_on")
        value = posted.get("value") if isinstance(posted, dict) else None
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                logger.warning("item %s has an unreadable posted_on %r", self.id, value)
        return self.created_at.date()


class Draft(Base):
    __tablename__ = "drafts"
    __table_args__ = (UniqueConstraint("item_id", "channel", "recipient", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(40))
    recipient: Mapped[str] = mapped_column(String(200), default="")
    version: Mapped[int] = mapped_column(default=1)
    subject: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    # Hook and angle used, naturalizer changes, and problems left for the user to fix.
    review: Mapped[JsonObject] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Interaction(Base):
    """What the user did with a lead outside the app: sent, replied, called, met."""

    __tablename__ = "interactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    note: Mapped[str] = mapped_column(Text, default="")
    happened_at: Mapped[datetime] = mapped_column(default=utcnow)


class Cv(Base):
    """One of the user's CV files; `summary` is the AI profile the user can correct."""

    __tablename__ = "cvs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    filename: Mapped[str] = mapped_column(String(200))
    # Deferred: lists and picks need the text, only a download needs the file.
    pdf: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    text: Mapped[str] = mapped_column(Text)
    summary: Mapped[JsonObject | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class VoiceSample(Base):
    """A message the user wrote themselves; held-out samples are kept for style evaluation."""

    __tablename__ = "voice_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel: Mapped[str] = mapped_column(String(20), index=True)
    text: Mapped[str] = mapped_column(Text)
    held_out: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class VoiceProfileRecord(Base):
    """The latest built voice profile per channel; rebuilt when samples or style prompts change."""

    __tablename__ = "voice_profiles"

    channel: Mapped[str] = mapped_column(String(20), primary_key=True)
    profile: Mapped[JsonObject]
    built_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Entity(Base):
    """A person or company in the global registry, shared by all projects."""

    __tablename__ = "entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[EntityKind] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(300))
    # FieldValue envelopes by field id; each value keeps the source it came from.
    profile: Mapped[JsonObject] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class EntityIdentifier(Base):
    """A normalized identifier; uniqueness across the registry is what makes exact merges safe."""

    __tablename__ = "entity_identifiers"
    __table_args__ = (UniqueConstraint("kind", "value"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[IdentifierKind]
    value: Mapped[str] = mapped_column(String(300))


class Sighting(Base):
    """One place an entity was seen; together they are the profile's source list."""

    __tablename__ = "sightings"
    __table_args__ = (UniqueConstraint("entity_id", "source_url", "quote"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    source_kind: Mapped[SourceKind]
    source_url: Mapped[str] = mapped_column(String(1000))
    quote: Mapped[str] = mapped_column(Text, default="")
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    seen_at: Mapped[datetime] = mapped_column(default=utcnow)


class Relation(Base):
    """A directed, evidenced link such as person works_at company."""

    __tablename__ = "relations"
    __table_args__ = (UniqueConstraint("from_id", "to_id", "type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    from_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    to_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str | None] = mapped_column(String(300))
    source_url: Mapped[str | None] = mapped_column(String(1000))
    quote: Mapped[str | None] = mapped_column(Text)


class ItemLink(Base):
    """Connects a lead to the people and companies around it, each with a role."""

    __tablename__ = "item_links"
    __table_args__ = (UniqueConstraint("item_id", "entity_id", "role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(40))


class MergeSuggestion(Base):
    """Two entities that might be one; only the user merges them."""

    __tablename__ = "merge_suggestions"
    __table_args__ = (UniqueConstraint("entity_a_id", "entity_b_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_a_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    entity_b_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    reason: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
