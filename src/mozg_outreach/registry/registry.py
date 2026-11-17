"""The global registry of people and companies: resolve sightings to entities and merge them."""

import logging

from pydantic import BaseModel, Field
from sqlalchemy import delete, or_, select, tuple_
from sqlalchemy.orm import Session

from mozg_outreach.domain import EntityKind, FieldValue, IdentifierKind, SourceKind
from mozg_outreach.registry.identifiers import Identifier
from mozg_outreach.storage.models import (
    Entity,
    EntityIdentifier,
    ItemLink,
    MergeSuggestion,
    RejectedIdentifier,
    Relation,
    Sighting,
)

logger = logging.getLogger(__name__)


class SightingInput(BaseModel):
    source_kind: SourceKind
    source_url: str = Field(min_length=1)
    quote: str = ""
    project_id: int | None = None
    run_id: int | None = None


class Observation(BaseModel):
    """What one source says about one person or company."""

    kind: EntityKind
    name: str = Field(min_length=1)
    identifiers: list[Identifier] = Field(default_factory=list)
    fields: dict[str, FieldValue] = Field(default_factory=dict)
    sighting: SightingInput | None = None


class RelationInput(BaseModel):
    from_id: int
    to_id: int
    type: str = Field(min_length=1, max_length=40)
    detail: str | None = None
    source_url: str | None = None
    quote: str | None = None


class EntityRegistry:
    def __init__(self, session: Session) -> None:
        self._session = session

    def observe(self, observation: Observation) -> Entity:
        """Find or create the entity; entities sharing an exact identifier are merged."""
        owners = [e for e in self._owners(observation.identifiers) if e.kind == observation.kind]
        if owners:
            entity = min(owners, key=lambda e: e.id)
        else:
            entity = Entity(kind=observation.kind, name=observation.name.strip(), profile={})
            self._session.add(entity)
            self._session.flush()
            logger.info("registry: new %s %r", entity.kind, entity.name)
        self.attach(entity, observation.identifiers)
        entity.profile = _fill_gaps(entity.profile, observation.fields)
        if observation.sighting:
            self.add_sighting(entity, observation.sighting)
        self._session.flush()
        return entity

    def refresh(self, entity: Entity, fields: dict[str, FieldValue]) -> None:
        """A search the user asked for: its sourced values replace what the profile had."""
        fresh = {k: v.model_dump(mode="json") for k, v in fields.items() if v.value is not None}
        entity.profile = {**entity.profile, **fresh}
        self._session.flush()

    def attach(self, entity: Entity, identifiers: list[Identifier]) -> Entity:
        """Give `entity` these identifiers, merging in any other entity that already has one.

        An identifier once shown to be someone else's is skipped, so no source adds it back."""
        rejected = self._rejected(entity.id)
        identifiers = [i for i in identifiers if (i.kind, i.value) not in rejected]
        for owner in self._owners(identifiers):
            if owner.id == entity.id:
                continue
            if owner.kind != entity.kind:
                # An email or domain can't be both a person and a company; leave it with its owner.
                logger.warning(
                    "registry: %r shares an identifier with %s %d",
                    entity.name,
                    owner.kind,
                    owner.id,
                )
                continue
            self.merge(entity, owner)
        for identifier in identifiers:
            owner_id = self._session.scalar(
                select(EntityIdentifier.entity_id).where(
                    EntityIdentifier.kind == identifier.kind,
                    EntityIdentifier.value == identifier.value,
                )
            )
            if owner_id is None:
                self._session.add(
                    EntityIdentifier(
                        entity_id=entity.id, kind=identifier.kind, value=identifier.value
                    )
                )
        self._session.flush()
        return entity

    def reject(self, entity: Entity, identifier: Identifier, source_url: str, quote: str) -> bool:
        """Take an identifier off `entity` for good; False when the entity didn't have it."""
        row = self._session.scalar(
            select(EntityIdentifier).where(
                EntityIdentifier.entity_id == entity.id,
                EntityIdentifier.kind == identifier.kind,
                EntityIdentifier.value == identifier.value,
            )
        )
        if row is None:
            return False
        self._session.delete(row)
        if (identifier.kind, identifier.value) not in self._rejected(entity.id):
            self._session.add(
                RejectedIdentifier(
                    entity_id=entity.id,
                    kind=identifier.kind,
                    value=identifier.value,
                    source_url=source_url,
                    quote=quote,
                )
            )
        self._session.flush()
        logger.info(
            "registry: %s %s is not %s %d",
            identifier.kind,
            identifier.value,
            entity.kind,
            entity.id,
        )
        return True

    def rejected(self, entity_id: int) -> list[Identifier]:
        return [Identifier(kind=k, value=v) for k, v in sorted(self._rejected(entity_id))]

    def add_sighting(self, entity: Entity, sighting: SightingInput) -> None:
        if not self._has_sighting(entity.id, sighting.source_url, sighting.quote):
            self._session.add(Sighting(entity_id=entity.id, **sighting.model_dump()))
            self._session.flush()

    def link(self, item_id: int, entity: Entity, role: str) -> None:
        exists = self._session.scalar(
            select(ItemLink.id).where(
                ItemLink.item_id == item_id, ItemLink.entity_id == entity.id, ItemLink.role == role
            )
        )
        if exists is None:
            self._session.add(ItemLink(item_id=item_id, entity_id=entity.id, role=role))
            self._session.flush()

    def relate(self, relation: RelationInput) -> None:
        if relation.from_id == relation.to_id:
            raise ValueError(f"entity {relation.from_id} can't relate to itself")
        existing = self._session.scalar(
            select(Relation).where(
                Relation.from_id == relation.from_id,
                Relation.to_id == relation.to_id,
                Relation.type == relation.type,
            )
        )
        if existing is None:
            self._session.add(Relation(**relation.model_dump()))
        elif existing.source_url is None and relation.source_url:
            existing.source_url, existing.quote = relation.source_url, relation.quote
        self._session.flush()

    def merge(self, keep: Entity, drop: Entity) -> Entity:
        """Move everything from `drop` onto `keep`, then delete `drop`."""
        if keep.id == drop.id or keep.kind != drop.kind:
            raise ValueError(
                f"can't merge entity {drop.id} ({drop.kind}) into {keep.id} ({keep.kind})"
            )
        logger.info(
            "registry: merging %s %d %r into %d %r",
            keep.kind,
            drop.id,
            drop.name,
            keep.id,
            keep.name,
        )
        for identifier in self._session.scalars(
            select(EntityIdentifier).where(EntityIdentifier.entity_id == drop.id)
        ):
            identifier.entity_id = keep.id
        for sighting in list(
            self._session.scalars(select(Sighting).where(Sighting.entity_id == drop.id))
        ):
            if self._has_sighting(keep.id, sighting.source_url, sighting.quote):
                self._session.delete(sighting)
            else:
                sighting.entity_id = keep.id
        self._move_relations(keep.id, drop.id)
        known = self._rejected(keep.id)
        for row in list(
            self._session.scalars(
                select(RejectedIdentifier).where(RejectedIdentifier.entity_id == drop.id)
            )
        ):
            if (row.kind, row.value) in known:
                self._session.delete(row)
            else:
                row.entity_id = keep.id
        for link in list(
            self._session.scalars(select(ItemLink).where(ItemLink.entity_id == drop.id))
        ):
            self._session.delete(link)
            self._session.flush()
            self.link(link.item_id, keep, link.role)
        self._session.execute(
            delete(MergeSuggestion).where(
                or_(MergeSuggestion.entity_a_id == drop.id, MergeSuggestion.entity_b_id == drop.id)
            )
        )
        keep.profile = _fill_gaps(keep.profile, _profile_values(drop.profile))
        self._session.delete(drop)
        self._session.flush()
        return keep

    def delete(self, entity: Entity) -> None:
        """Forget an entity: the database cascade removes identifiers, sightings and links."""
        logger.info("registry: deleting %s %d", entity.kind, entity.id)
        self._session.delete(entity)
        self._session.flush()
        self._session.expire_all()

    def _owners(self, identifiers: list[Identifier]) -> list[Entity]:
        if not identifiers:
            return []
        keys = [(i.kind, i.value) for i in identifiers]
        return list(
            self._session.scalars(
                select(Entity)
                .join(EntityIdentifier, EntityIdentifier.entity_id == Entity.id)
                .where(tuple_(EntityIdentifier.kind, EntityIdentifier.value).in_(keys))
                .distinct()
            )
        )

    def _rejected(self, entity_id: int) -> set[tuple[IdentifierKind, str]]:
        rows = self._session.execute(
            select(RejectedIdentifier.kind, RejectedIdentifier.value).where(
                RejectedIdentifier.entity_id == entity_id
            )
        )
        return {(kind, value) for kind, value in rows}

    def _has_sighting(self, entity_id: int, source_url: str, quote: str) -> bool:
        found = self._session.scalar(
            select(Sighting.id).where(
                Sighting.entity_id == entity_id,
                Sighting.source_url == source_url,
                Sighting.quote == quote,
            )
        )
        return found is not None

    def _move_relations(self, keep_id: int, drop_id: int) -> None:
        relations = self._session.scalars(
            select(Relation).where(or_(Relation.from_id == drop_id, Relation.to_id == drop_id))
        )
        for relation in list(relations):
            from_id = keep_id if relation.from_id == drop_id else relation.from_id
            to_id = keep_id if relation.to_id == drop_id else relation.to_id
            evidence = RelationInput(
                from_id=from_id,
                to_id=to_id,
                type=relation.type,
                detail=relation.detail,
                source_url=relation.source_url,
                quote=relation.quote,
            )
            self._session.delete(relation)
            self._session.flush()
            # A relation between the two merged entities would point at itself; drop it.
            if from_id != to_id:
                self.relate(evidence)


def _profile_values(profile: dict[str, object]) -> dict[str, FieldValue]:
    return {k: FieldValue.model_validate(v) for k, v in profile.items()}


def _fill_gaps(profile: dict[str, object], fields: dict[str, FieldValue]) -> dict[str, object]:
    """Known values stay; a new source only fills fields that are missing or empty."""
    merged = dict(profile)
    for field_id, value in fields.items():
        current = merged.get(field_id)
        if value.value is not None and (current is None or _is_empty(current)):
            merged[field_id] = value.model_dump(mode="json")
    return merged


def _is_empty(stored: object) -> bool:
    return isinstance(stored, dict) and stored.get("value") in (None, "", [])
