"""Possible duplicates without a shared identifier become suggestions; only the user merges."""

import logging
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from mozg_outreach.domain import EntityKind, FieldValue
from mozg_outreach.registry.identifiers import name_key
from mozg_outreach.registry.registry import EntityRegistry
from mozg_outreach.storage.models import Entity, MergeSuggestion, Relation

logger = logging.getLogger(__name__)

NAME_SIMILARITY = 0.88
LOCATION_FIELDS = ("location", "city")


class MergeSuggester:
    def __init__(self, session: Session) -> None:
        self._session = session

    def suggest_for(self, entity: Entity) -> list[MergeSuggestion]:
        """Record suggestions pairing `entity` with look-alikes; rejected pairs stay rejected."""
        created: list[MergeSuggestion] = []
        others = self._session.scalars(
            select(Entity).where(Entity.kind == entity.kind, Entity.id != entity.id)
        )
        for other in others:
            reason = self._reason(entity, other)
            if reason and not self._known_pair(entity.id, other.id):
                low, high = sorted((entity.id, other.id))
                suggestion = MergeSuggestion(entity_a_id=low, entity_b_id=high, reason=reason)
                self._session.add(suggestion)
                created.append(suggestion)
                logger.info("registry: suggest merging %d and %d (%s)", low, high, reason)
        self._session.flush()
        return created

    def accept(self, suggestion_id: int) -> Entity:
        suggestion = self._get(suggestion_id)
        keep = self._session.get(Entity, suggestion.entity_a_id)
        drop = self._session.get(Entity, suggestion.entity_b_id)
        if keep is None or drop is None:
            raise LookupError(f"merge suggestion {suggestion_id} points at a deleted entity")
        return EntityRegistry(self._session).merge(keep, drop)

    def reject(self, suggestion_id: int) -> None:
        self._get(suggestion_id).status = "rejected"
        self._session.flush()

    def _reason(self, entity: Entity, other: Entity) -> str | None:
        similar = _similar_names(entity.name, other.name)
        if not similar:
            return None
        if entity.kind == EntityKind.COMPANY:
            return f"similar names: {entity.name!r} and {other.name!r}"
        # A common name alone is weak evidence for a person; require a second shared fact.
        shared = self._employers(entity.id) & self._employers(other.id)
        if shared:
            return f"similar names {entity.name!r} and {other.name!r} at the same company"
        place = _location(entity)
        if place and place == _location(other):
            return f"similar names {entity.name!r} and {other.name!r} in {place}"
        return None

    def _employers(self, entity_id: int) -> set[int]:
        return set(
            self._session.scalars(
                select(Relation.to_id).where(
                    Relation.from_id == entity_id, Relation.type == "works_at"
                )
            )
        )

    def _known_pair(self, a: int, b: int) -> bool:
        low, high = sorted((a, b))
        found = self._session.scalar(
            select(MergeSuggestion.id).where(
                MergeSuggestion.entity_a_id == low, MergeSuggestion.entity_b_id == high
            )
        )
        return found is not None

    def _get(self, suggestion_id: int) -> MergeSuggestion:
        suggestion = self._session.get(MergeSuggestion, suggestion_id)
        if suggestion is None or suggestion.status != "pending":
            raise LookupError(f"no pending merge suggestion {suggestion_id}")
        return suggestion


def _similar_names(a: str, b: str) -> bool:
    key_a, key_b = name_key(a), name_key(b)
    if not key_a or not key_b:
        return False
    if SequenceMatcher(None, key_a, key_b).ratio() >= NAME_SIMILARITY:
        return True
    words_a, words_b = key_a.split(), key_b.split()
    # "A Kask" and "Anna Kask": same surname, matching first initial.
    return (
        len(words_a) > 1
        and len(words_b) > 1
        and words_a[-1] == words_b[-1]
        and words_a[0][0] == words_b[0][0]
    )


def _location(entity: Entity) -> str | None:
    for field in LOCATION_FIELDS:
        if field in entity.profile:
            value = FieldValue.model_validate(entity.profile[field]).value
            if isinstance(value, str) and value.strip():
                return value.strip().casefold()
    return None
