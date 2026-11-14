import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mozg_outreach.domain import EntityKind, FieldValue
from mozg_outreach.registry.registry import EntityRegistry, Observation, RelationInput
from mozg_outreach.registry.suggestions import MergeSuggester
from mozg_outreach.storage.models import Entity, MergeSuggestion


@pytest.fixture
def registry(session: Session) -> EntityRegistry:
    return EntityRegistry(session)


@pytest.fixture
def suggester(session: Session) -> MergeSuggester:
    return MergeSuggester(session)


def entity(registry: EntityRegistry, kind: EntityKind, name: str, **fields: str) -> Entity:
    values = {k: FieldValue(value=v) for k, v in fields.items()}
    return registry.observe(Observation(kind=kind, name=name, fields=values))


class TestCompanies:
    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("Katana", "Katana OU"),
            ("Blackwall (formerly BotGuard)", "Blackwall"),
            ("Veriff", "Veriff AS"),
        ],
    )
    def test_similar_names_are_suggested(
        self, registry: EntityRegistry, suggester: MergeSuggester, a: str, b: str
    ) -> None:
        first = entity(registry, EntityKind.COMPANY, a)
        second = entity(registry, EntityKind.COMPANY, b)
        [suggestion] = suggester.suggest_for(second)
        assert (suggestion.entity_a_id, suggestion.entity_b_id) == (first.id, second.id)
        assert suggestion.status == "pending"

    def test_different_names_are_not(
        self, registry: EntityRegistry, suggester: MergeSuggester
    ) -> None:
        entity(registry, EntityKind.COMPANY, "Katana")
        assert suggester.suggest_for(entity(registry, EntityKind.COMPANY, "Salv")) == []


class TestPeople:
    def test_name_alone_is_not_enough(
        self, registry: EntityRegistry, suggester: MergeSuggester
    ) -> None:
        entity(registry, EntityKind.PERSON, "Anna Kask")
        assert suggester.suggest_for(entity(registry, EntityKind.PERSON, "A. Kask")) == []

    def test_shared_employer_makes_a_suggestion(
        self, registry: EntityRegistry, suggester: MergeSuggester
    ) -> None:
        acme = entity(registry, EntityKind.COMPANY, "Acme")
        first = entity(registry, EntityKind.PERSON, "Anna Kask")
        second = entity(registry, EntityKind.PERSON, "A. Kask")
        for person in (first, second):
            registry.relate(RelationInput(from_id=person.id, to_id=acme.id, type="works_at"))
        [suggestion] = suggester.suggest_for(second)
        assert "same company" in suggestion.reason

    def test_same_location_makes_a_suggestion(
        self, registry: EntityRegistry, suggester: MergeSuggester
    ) -> None:
        entity(registry, EntityKind.PERSON, "Anna Kask", city="Tallinn")
        second = entity(registry, EntityKind.PERSON, "Anna Kask", city="tallinn")
        [suggestion] = suggester.suggest_for(second)
        assert "tallinn" in suggestion.reason


class TestDecisions:
    def test_rejected_pair_is_not_suggested_again(
        self, registry: EntityRegistry, suggester: MergeSuggester
    ) -> None:
        entity(registry, EntityKind.COMPANY, "Katana")
        second = entity(registry, EntityKind.COMPANY, "Katana OU")
        [suggestion] = suggester.suggest_for(second)
        suggester.reject(suggestion.id)
        assert suggester.suggest_for(second) == []
        with pytest.raises(LookupError, match="no pending"):
            suggester.accept(suggestion.id)

    def test_accept_merges_into_the_older_entity(
        self, registry: EntityRegistry, suggester: MergeSuggester, session: Session
    ) -> None:
        first = entity(registry, EntityKind.COMPANY, "Katana", sector="SaaS")
        second = entity(registry, EntityKind.COMPANY, "Katana OU", city="Tallinn")
        [suggestion] = suggester.suggest_for(second)

        kept = suggester.accept(suggestion.id)

        assert kept.id == first.id
        assert [e.id for e in session.scalars(select(Entity))] == [first.id]
        assert set(kept.profile) == {"sector", "city"}
        assert list(session.scalars(select(MergeSuggestion))) == []
