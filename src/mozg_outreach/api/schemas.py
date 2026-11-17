"""Request and response bodies; rows map onto them with from_attributes."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from mozg_outreach.cvs.models import CvSummary
from mozg_outreach.domain import EntityKind, IdentifierKind, ItemKind, ProjectMode, RunStatus
from mozg_outreach.letters.models import Tone
from mozg_outreach.naturalize.models import Channel
from mozg_outreach.pipelines.biz.approach_models import ApproachStep
from mozg_outreach.pipelines.biz.site_contacts import profile_url
from mozg_outreach.storage.models import JsonObject
from mozg_outreach.storage.repositories import LeadActivity


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


class SearchReadiness(BaseModel):
    pipeline: str
    blocker: str | None


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
    enriched: bool
    enriched_at: datetime | None
    archived_at: datetime | None
    dated: date = Field(description="Posted on, or the day a run first found the lead.")
    updated_at: datetime
    activity: LeadActivity | None = None
    approach_step: ApproachStep | None = None


class DraftOut(Row):
    id: int
    item_id: int
    channel: str
    recipient: str
    step: int | None
    version: int
    subject: str | None
    body: str
    review: JsonObject
    created_at: datetime


class StepDoneIn(BaseModel):
    on: date | None = Field(default=None, description="When it was done; today when null.")


class DraftRevisionIn(BaseModel):
    suggestion: str = Field(min_length=1, max_length=2000)


class DraftEdit(BaseModel):
    subject: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=1)


class MailThreadOut(Row):
    """An email letter's Gmail draft and what the mailbox shows became of it."""

    id: int
    item_id: int
    recipient: str
    step: int | None
    draft_id: int
    address: str
    pushed_at: datetime
    sent_at: datetime | None
    replied_at: datetime | None
    bounced_at: datetime | None
    auto_replied_at: datetime | None
    sent_body: str | None = Field(description="What the user sent, as Gmail has it.")
    edited: float | None = Field(
        default=None, description="Share of the draft's words changed before sending, 0 to 1."
    )
    warnings: list[str] = Field(default_factory=list, description="Only on a push answer.")


class MailStatusOut(BaseModel):
    """The Gmail account drafts go to; null when Gmail isn't set up."""

    address: str | None


class InteractionIn(BaseModel):
    # "declined" is the user turning the lead down; "refused" is the other side doing so.
    kind: Literal[
        "sent",
        "replied",
        "out_of_office",
        "not_now",
        "opted_out",
        "bounced",
        "called",
        "met",
        "declined",
        "refused",
        "note",
    ]
    note: str = ""
    happened_at: datetime | None = None
    until: date | None = Field(
        default=None, description="Out of office: the return date. Not now: when to come back."
    )


class InteractionOut(Row):
    id: int
    kind: str
    note: str
    happened_at: datetime
    until: date | None


class LinkedEntity(BaseModel):
    entity_id: int
    kind: EntityKind
    name: str
    role: str


class ItemDetail(ItemOut):
    raw: JsonObject
    details: JsonObject
    drafts: list[DraftOut]
    mail: list[MailThreadOut]
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
    activity: LeadActivity | None


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


class BriefRequest(BaseModel):
    """Biz projects send their company site, seeker projects the CV."""

    site_url: HttpUrl | None = None
    cv_text: str | None = Field(default=None, min_length=200)
    note: str = Field(default="", max_length=4000)


class BriefDraftOut(BaseModel):
    brief: JsonObject = Field(description="A draft for the form; it is not saved.")
    concerns: list[str]
    field_version: int | None = Field(description="The drafted field spec, not yet accepted.")
    cost_usd: float


class CvOut(Row):
    id: int
    name: str
    filename: str
    summary: CvSummary | None
    created_at: datetime


class CvUploadOut(BaseModel):
    cv: CvOut
    concerns: list[str]
    cost_usd: float


class CvPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    summary: CvSummary | None = None


class ArchiveIn(BaseModel):
    item_ids: list[int] = Field(min_length=1, max_length=500)
    archived: bool = Field(description="False restores the leads to the report.")


class ProfileLinkIn(BaseModel):
    name: str = Field(min_length=1, description="The key person's name as the dossier has it.")
    url: str

    @field_validator("url")
    @classmethod
    def _linkedin(cls, url: str) -> str:
        canonical = profile_url(url)
        if canonical is None:
            raise ValueError(f"not a LinkedIn profile link: {url!r}")
        return canonical


class CvPick(BaseModel):
    cv_id: int | None = Field(description="Null clears the pick, so the next letter picks again.")
