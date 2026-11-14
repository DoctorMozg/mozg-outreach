"""Request and response bodies; rows map onto them with from_attributes."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mozg_outreach.domain import EntityKind, IdentifierKind, ItemKind, ProjectMode, RunStatus
from mozg_outreach.letters.models import Tone
from mozg_outreach.naturalize.models import Channel
from mozg_outreach.storage.models import JsonObject


class Row(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    mode: ProjectMode
    sender_name: str | None = Field(default=None, max_length=100)
    sender_tone: Tone = "neutral"
    brief: JsonObject | None = None


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    sender_name: str | None = Field(default=None, max_length=100)
    sender_tone: Tone | None = None
    brief: JsonObject | None = None


class ProjectOut(Row):
    id: int
    name: str
    mode: ProjectMode
    sender_name: str | None
    sender_tone: str
    brief: JsonObject | None
    created_at: datetime


class FieldSpecOut(Row):
    version: int
    spec: JsonObject
    accepted_at: datetime | None
    created_at: datetime


class SourceIn(BaseModel):
    kind: str
    config: JsonObject = Field(default_factory=dict)
    enabled: bool = True


class SourcePatch(BaseModel):
    config: JsonObject | None = None
    enabled: bool | None = None


class SourceOut(Row):
    id: int
    project_id: int
    kind: str
    config: JsonObject
    enabled: bool
    cursor: str | None


class SourceKindOut(BaseModel):
    kind: str
    modes: list[ProjectMode]
    note: str


class RunStart(BaseModel):
    options: JsonObject = Field(default_factory=dict, description="The search flow's options.")


class RunOut(Row):
    id: int
    project_id: int
    pipeline: str
    status: RunStatus
    phase: str | None
    pending_gate: JsonObject | None
    state: JsonObject
    cost_usd: float
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class RunEventOut(Row):
    seq: int
    kind: str
    payload: JsonObject
    created_at: datetime


class ItemOut(Row):
    id: int
    project_id: int
    kind: ItemKind
    title: str
    fields: JsonObject
    score_total: float | None
    score: JsonObject | None
    selected: bool
    updated_at: datetime


class ItemPatch(BaseModel):
    selected: bool


class DraftOut(Row):
    id: int
    item_id: int
    channel: str
    recipient: str
    version: int
    subject: str | None
    body: str
    review: JsonObject
    created_at: datetime


class DraftEdit(BaseModel):
    subject: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=1)


class InteractionIn(BaseModel):
    kind: Literal["sent", "replied", "called", "met", "declined", "note"]
    note: str = ""
    happened_at: datetime | None = None


class InteractionOut(Row):
    id: int
    kind: str
    note: str
    happened_at: datetime


class LinkedEntity(BaseModel):
    entity_id: int
    kind: EntityKind
    name: str
    role: str


class ItemDetail(ItemOut):
    raw: JsonObject
    details: JsonObject
    drafts: list[DraftOut]
    interactions: list[InteractionOut]
    entities: list[LinkedEntity]


class SampleIn(BaseModel):
    text: str = Field(min_length=20)
    held_out: bool = False


class SampleOut(Row):
    id: int
    channel: Channel
    text: str
    held_out: bool
    created_at: datetime


class ProfileBuild(BaseModel):
    style_prompts: list[str] = Field(default_factory=list, description="The user's own rules.")


class VoiceProfileOut(BaseModel):
    channel: Channel
    profile: JsonObject | None
    rendered: str | None


class IdentifierOut(Row):
    kind: IdentifierKind
    value: str


class SightingOut(Row):
    source_kind: str
    source_url: str
    quote: str
    project_id: int | None
    seen_at: datetime


class RelationOut(BaseModel):
    type: str
    direction: Literal["out", "in"]
    other_id: int
    other_name: str
    detail: str | None
    source_url: str | None
    quote: str | None


class LeadOut(BaseModel):
    item_id: int
    project_id: int
    title: str
    role: str


class EntityOut(Row):
    id: int
    kind: EntityKind
    name: str
    updated_at: datetime


class EntityDetail(EntityOut):
    profile: JsonObject
    identifiers: list[IdentifierOut]
    sightings: list[SightingOut]
    relations: list[RelationOut]
    leads: list[LeadOut]


class GraphNode(BaseModel):
    id: str = Field(description="'e<id>' for entities, 'i<id>' for items.")
    kind: Literal["person", "company", "lead"]
    label: str


class GraphEdge(BaseModel):
    source: str
    target: str
    label: str


class GraphOut(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class MergeSuggestionOut(BaseModel):
    id: int
    reason: str
    status: str
    a: EntityOut
    b: EntityOut
