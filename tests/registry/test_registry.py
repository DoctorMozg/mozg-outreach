import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mozg_outreach.domain import (
    EntityKind,
    FieldValue,
    IdentifierKind,
    ItemCandidate,
    ItemKind,
    SourceKind,
)
from mozg_outreach.registry.identifiers import Identifier, normalize
from mozg_outreach.registry.registry import (
    EntityRegistry,
    Observation,
    RelationInput,
    SightingInput,
)
from mozg_outreach.storage.models import (
    Entity,
    EntityIdentifier,
    ItemLink,
    MergeSuggestion,
    Project,
    Relation,
    Run,
    Sighting,
)
from mozg_outreach.storage.repositories import ItemRepository


def ident(kind: IdentifierKind, raw: str) -> Identifier:
    identifier = normalize(kind, raw)
    assert identifier is not None
    return identifier


def seen(url: str, quote: str = "") -> SightingInput:
    return SightingInput(source_kind=SourceKind.WEB, source_url=url, quote=quote)


def person(
    name: str, *ids: Identifier, url: str = "https://acme.io/team", **fields: str
) -> Observation:
    return Observation(
        kind=EntityKind.PERSON,
        name=name,
        identifiers=list(ids),
        fields={k: FieldValue(value=v, source_url=url) for k, v in fields.items()},
        sighting=seen(url, name),
    )


@pytest.fixture
def registry(session: Session) -> EntityRegistry:
    return EntityRegistry(session)


def count[TRow](session: Session, model: type[TRow]) -> int:
    return len(list(session.scalars(select(model))))


class TestObserve:
    def test_same_identifier_is_one_entity_with_all_sources(
        self, registry: EntityRegistry, session: Session
    ) -> None:
        email = ident(IdentifierKind.EMAIL, "anna@acme.io")
        first = registry.observe(person("Anna Kask", email, title="CTO"))
        second = registry.observe(
            person(
                "Anna K.",
                email,
                url="https://t.me/estonia_devops/42",
                title="VP Eng",
                city="Tallinn",
            )
        )

        assert second.id == first.id
        assert second.name == "Anna Kask"
        assert FieldValue.model_validate(second.profile["title"]).value == "CTO"
        assert FieldValue.model_validate(second.profile["city"]).value == "Tallinn"
        assert count(session, Sighting) == 2

    def test_no_shared_identifier_means_no_merge(self, registry: EntityRegistry) -> None:
        a = registry.observe(person("Anna Kask", ident(IdentifierKind.EMAIL, "anna@acme.io")))
        b = registry.observe(person("Anna Kask", ident(IdentifierKind.EMAIL, "anna@other.io")))
        assert a.id != b.id

    def test_observation_bridging_two_entities_merges_them(
        self, registry: EntityRegistry, session: Session
    ) -> None:
        email = ident(IdentifierKind.EMAIL, "anna@acme.io")
        linkedin = ident(IdentifierKind.LINKEDIN, "linkedin.com/in/anna-kask")
        a = registry.observe(person("Anna Kask", email))
        b = registry.observe(person("Anna Kask", linkedin, url="https://linkedin.com/in/anna-kask"))
        assert a.id != b.id

        merged = registry.observe(person("Anna Kask", email, linkedin, url="https://acme.io/about"))

        assert merged.id == min(a.id, b.id)
        assert count(session, Entity) == 1
        values = set(session.scalars(select(EntityIdentifier.value)))
        assert values == {"anna@acme.io", "linkedin.com/in/anna-kask"}
        assert count(session, Sighting) == 3

    def test_identifier_owned_by_other_kind_is_not_merged(
        self, registry: EntityRegistry, session: Session
    ) -> None:
        email = ident(IdentifierKind.EMAIL, "info@acme.io")
        company = registry.observe(
            Observation(kind=EntityKind.COMPANY, name="Acme", identifiers=[email])
        )
        someone = registry.observe(person("Front Desk", email))
        assert someone.id != company.id
        owner = session.scalar(
            select(EntityIdentifier.entity_id).where(EntityIdentifier.value == "info@acme.io")
        )
        assert owner == company.id


class TestMerge:
    @pytest.fixture
    def graph(
        self, registry: EntityRegistry, session: Session, project: Project, run: Run
    ) -> tuple[Entity, Entity, Entity, int]:
        keep = registry.observe(person("Anna Kask", ident(IdentifierKind.EMAIL, "anna@acme.io")))
        drop = registry.observe(
            person("A. Kask", ident(IdentifierKind.TELEGRAM, "@anna_kask"), url="https://t.me/x/1")
        )
        company = registry.observe(
            Observation(
                kind=EntityKind.COMPANY,
                name="Acme",
                identifiers=[ident(IdentifierKind.DOMAIN, "acme.io")],
            )
        )
        item = ItemRepository(session).upsert(
            project.id,
            run.id,
            ItemCandidate(kind=ItemKind.COMPANY, dedup_key="acme.io", title="Acme"),
        )
        for entity in (keep, drop):
            registry.relate(RelationInput(from_id=entity.id, to_id=company.id, type="works_at"))
            registry.link(item.id, entity, "contact")
        registry.relate(RelationInput(from_id=keep.id, to_id=drop.id, type="knows"))
        session.add(MergeSuggestion(entity_a_id=keep.id, entity_b_id=drop.id, reason="same name"))
        session.flush()
        return keep, drop, company, item.id

    def test_merge_moves_everything_and_dedupes(
        self, registry: EntityRegistry, session: Session, graph: tuple[Entity, Entity, Entity, int]
    ) -> None:
        keep, drop, company, item_id = graph
        drop_id = drop.id

        registry.merge(keep, drop)

        assert session.get(Entity, drop_id) is None
        assert set(session.scalars(select(EntityIdentifier.entity_id))) == {keep.id, company.id}
        assert set(session.scalars(select(Sighting.entity_id))) == {keep.id}
        assert count(session, Sighting) == 2
        relations = [(r.from_id, r.to_id, r.type) for r in session.scalars(select(Relation))]
        assert relations == [(keep.id, company.id, "works_at")]
        links = [(link.item_id, link.entity_id) for link in session.scalars(select(ItemLink))]
        assert links == [(item_id, keep.id)]
        assert count(session, MergeSuggestion) == 0

    def test_merge_rejects_different_kinds(
        self, registry: EntityRegistry, graph: tuple[Entity, Entity, Entity, int]
    ) -> None:
        keep, _, company, _ = graph
        with pytest.raises(ValueError, match="can't merge"):
            registry.merge(keep, company)

    def test_delete_forgets_the_person_everywhere(
        self, registry: EntityRegistry, session: Session, graph: tuple[Entity, Entity, Entity, int]
    ) -> None:
        keep, drop, company, _ = graph
        registry.delete(keep)

        assert {e.id for e in session.scalars(select(Entity))} == {drop.id, company.id}
        remaining = list(session.scalars(select(EntityIdentifier.value)))
        assert "anna@acme.io" not in remaining
        assert all(
            r.from_id != keep.id and r.to_id != keep.id for r in session.scalars(select(Relation))
        )
        assert all(link.entity_id != keep.id for link in session.scalars(select(ItemLink)))
        assert all(s.entity_id != keep.id for s in session.scalars(select(Sighting)))
        assert count(session, MergeSuggestion) == 0


def test_relation_to_self_is_rejected(registry: EntityRegistry) -> None:
    anna = registry.observe(person("Anna Kask"))
    with pytest.raises(ValueError, match="itself"):
        registry.relate(RelationInput(from_id=anna.id, to_id=anna.id, type="knows"))


class TestReject:
    WRONG = "https://linkedin.com/in/anna-kask-nurse"
    PROOF = ("https://linkedin.com/in/anna-kask-nurse", "Anna Kask - Nurse at Tartu Clinic")

    def identifiers(self, session: Session, entity: Entity) -> set[str]:
        rows = session.scalars(
            select(EntityIdentifier.value).where(EntityIdentifier.entity_id == entity.id)
        )
        return set(rows)

    def test_a_rejected_identifier_leaves_and_never_comes_back(
        self, registry: EntityRegistry, session: Session
    ) -> None:
        wrong = ident(IdentifierKind.LINKEDIN, self.WRONG)
        anna = registry.observe(person("Anna Kask", wrong))

        assert registry.reject(anna, wrong, *self.PROOF)
        registry.attach(anna, [wrong])
        again = registry.observe(person("Anna Kask", wrong))

        assert self.identifiers(session, anna) == set()
        assert registry.rejected(anna.id) == [wrong]
        # A later sighting with the wrong profile becomes its own entity, not Anna again.
        assert again.id != anna.id

    def test_rejecting_what_the_entity_lacks_changes_nothing(
        self, registry: EntityRegistry, session: Session
    ) -> None:
        anna = registry.observe(person("Anna Kask"))

        assert not registry.reject(anna, ident(IdentifierKind.LINKEDIN, self.WRONG), *self.PROOF)
        assert registry.rejected(anna.id) == []

    def test_a_merge_keeps_the_dropped_entitys_rejections(
        self, registry: EntityRegistry, session: Session
    ) -> None:
        wrong = ident(IdentifierKind.LINKEDIN, self.WRONG)
        keep = registry.observe(person("Anna Kask", ident(IdentifierKind.EMAIL, "anna@acme.io")))
        drop = registry.observe(person("A. Kask", wrong))
        registry.reject(drop, wrong, *self.PROOF)

        registry.merge(keep, drop)
        registry.attach(keep, [wrong])

        assert registry.rejected(keep.id) == [wrong]
        assert self.identifiers(session, keep) == {"anna@acme.io"}


def test_refresh_replaces_values_and_keeps_the_rest(registry: EntityRegistry) -> None:
    anna = registry.observe(person("Anna Kask", title="Engineer", location="Tallinn"))

    registry.refresh(anna, {"title": FieldValue(value="CTO", source_url="https://acme.io/about")})

    values = {k: FieldValue.model_validate(v).value for k, v in anna.profile.items()}
    assert values == {
        "title": "CTO",
        "location": "Tallinn",
    }
