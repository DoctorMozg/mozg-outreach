import logging

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mozg_outreach.domain import FieldSpec, FieldValue, ItemCandidate, ItemScore
from mozg_outreach.storage.models import (
    Draft,
    FieldSpecVersion,
    Interaction,
    Item,
    JsonObject,
    RunEvent,
    VoiceProfileRecord,
    VoiceSample,
    utcnow,
)

logger = logging.getLogger(__name__)


class FieldSpecConflictError(ValueError):
    """A new spec version would change the type of a field that runs already filled."""


class FieldSpecRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def propose(self, project_id: int, spec: FieldSpec) -> FieldSpecVersion:
        """Store `spec` as the next draft version, refusing type changes of accepted fields."""
        accepted = self.current(project_id)
        if accepted is not None:
            previous = FieldSpec.model_validate(accepted.spec).by_id()
            changed = [
                f"{f.id}: {previous[f.id].type} -> {f.type}"
                for f in spec.fields
                if f.id in previous and previous[f.id].type != f.type
            ]
            if changed:
                raise FieldSpecConflictError(f"field types can't change, add new ids: {changed}")

        latest = self._session.scalar(
            select(func.max(FieldSpecVersion.version)).where(
                FieldSpecVersion.project_id == project_id
            )
        )
        row = FieldSpecVersion(
            project_id=project_id,
            version=(latest or 0) + 1,
            spec=spec.model_dump(mode="json"),
            accepted_at=None,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def accept(self, project_id: int, version: int) -> FieldSpecVersion:
        row = self._session.scalar(
            select(FieldSpecVersion).where(
                FieldSpecVersion.project_id == project_id, FieldSpecVersion.version == version
            )
        )
        if row is None:
            raise LookupError(f"project {project_id} has no field spec version {version}")
        if row.accepted_at is None:
            row.accepted_at = utcnow()
            logger.info("project %d accepted field spec v%d", project_id, version)
        return row

    def current(self, project_id: int) -> FieldSpecVersion | None:
        return self._session.scalar(
            select(FieldSpecVersion)
            .where(
                FieldSpecVersion.project_id == project_id,
                FieldSpecVersion.accepted_at.is_not(None),
            )
            .order_by(FieldSpecVersion.version.desc())
            .limit(1)
        )


class RunEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, run_id: int, kind: str, payload: JsonObject) -> RunEvent:
        last = self._session.scalar(select(func.max(RunEvent.seq)).where(RunEvent.run_id == run_id))
        event = RunEvent(run_id=run_id, seq=(last or 0) + 1, kind=kind, payload=payload)
        self._session.add(event)
        self._session.flush()
        return event

    def after(self, run_id: int, seq: int = 0) -> list[RunEvent]:
        return list(
            self._session.scalars(
                select(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.seq > seq)
                .order_by(RunEvent.seq)
            )
        )


class ItemRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(self, project_id: int, run_id: int, candidate: ItemCandidate) -> Item:
        """Insert a new item or merge its fields into the existing one with the same dedup key."""
        item = self._session.scalar(
            select(Item).where(Item.project_id == project_id, Item.dedup_key == candidate.dedup_key)
        )
        if item is None:
            item = Item(
                project_id=project_id,
                last_run_id=run_id,
                kind=candidate.kind,
                dedup_key=candidate.dedup_key,
                title=candidate.title,
                fields={},
            )
            self._session.add(item)
        else:
            item.title = candidate.title
            item.last_run_id = run_id
        self.merge_fields(item, candidate.fields)
        return item

    def merge_fields(self, item: Item, fields: dict[str, FieldValue]) -> None:
        dumped: JsonObject = {k: v.model_dump(mode="json") for k, v in fields.items()}
        item.fields = {**item.fields, **dumped}
        self._session.flush()

    def set_detail(self, item: Item, name: str, detail: BaseModel | None) -> None:
        """Store one pipeline section under `name`; None removes it."""
        details = {k: v for k, v in item.details.items() if k != name}
        if detail is not None:
            details[name] = detail.model_dump(mode="json")
        item.details = details
        self._session.flush()

    def set_score(self, item: Item, score: ItemScore) -> None:
        item.score_total = score.total
        item.score = score.model_dump(mode="json")
        self._session.flush()

    def report(self, project_id: int) -> list[Item]:
        """Items best first; unscored items go last."""
        return list(
            self._session.scalars(
                select(Item)
                .where(Item.project_id == project_id)
                .order_by(Item.score_total.desc().nulls_last(), Item.title)
            )
        )


class VoiceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_sample(self, channel: str, text: str, *, held_out: bool = False) -> VoiceSample:
        sample = VoiceSample(channel=channel, text=text, held_out=held_out)
        self._session.add(sample)
        self._session.flush()
        return sample

    def samples(self, channel: str, *, held_out: bool = False) -> list[str]:
        return list(
            self._session.scalars(
                select(VoiceSample.text)
                .where(VoiceSample.channel == channel, VoiceSample.held_out == held_out)
                .order_by(VoiceSample.id)
            )
        )

    def save_profile(self, channel: str, profile: BaseModel) -> None:
        record = self._session.get(VoiceProfileRecord, channel)
        dumped = profile.model_dump(mode="json")
        if record is None:
            self._session.add(VoiceProfileRecord(channel=channel, profile=dumped))
        else:
            record.profile = dumped
        self._session.flush()

    def profile(self, channel: str) -> JsonObject | None:
        record = self._session.get(VoiceProfileRecord, channel)
        return record.profile if record else None


class NewDraft(BaseModel):
    channel: str
    recipient: str
    subject: str | None
    body: str
    review: BaseModel


class DraftRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, item_id: int, draft: NewDraft) -> Draft:
        """Regenerating a letter adds a version; earlier ones stay for comparison."""
        latest = self._session.scalar(
            select(func.max(Draft.version)).where(
                Draft.item_id == item_id,
                Draft.channel == draft.channel,
                Draft.recipient == draft.recipient,
            )
        )
        row = Draft(
            item_id=item_id,
            channel=draft.channel,
            recipient=draft.recipient,
            version=(latest or 0) + 1,
            subject=draft.subject,
            body=draft.body,
            review=draft.review.model_dump(mode="json"),
        )
        self._session.add(row)
        self._session.flush()
        return row

    def for_item(self, item_id: int) -> list[Draft]:
        return list(
            self._session.scalars(
                select(Draft)
                .where(Draft.item_id == item_id)
                .order_by(Draft.recipient, Draft.channel, Draft.version)
            )
        )


class InteractionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def log(self, item_id: int, kind: str, note: str = "") -> Interaction:
        if not kind.strip():
            raise ValueError("interaction kind is empty")
        entry = Interaction(item_id=item_id, kind=kind.strip(), note=note)
        self._session.add(entry)
        self._session.flush()
        return entry

    def history(self, item_id: int) -> list[Interaction]:
        return list(
            self._session.scalars(
                select(Interaction)
                .where(Interaction.item_id == item_id)
                .order_by(Interaction.happened_at, Interaction.id)
            )
        )
