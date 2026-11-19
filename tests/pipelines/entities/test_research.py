import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mozg_outreach.domain import EntityKind, FieldValue, IdentifierKind
from mozg_outreach.pipelines.entities.models import EntityFindings, FoundPerson, IdentifierCheck
from mozg_outreach.pipelines.entities.research import (
    EntityUpdate,
    accept_findings,
    research_input,
)
from mozg_outreach.pipelines.leads.models import SourcedFact
from mozg_outreach.registry.identifiers import Identifier, normalize
from mozg_outreach.registry.ingest import IngestContext
from mozg_outreach.registry.registry import EntityRegistry, Observation, RelationInput
from mozg_outreach.storage.models import Entity, EntityIdentifier, Project, Relation, Run
from tests.pipelines.leads.conftest import company

TEAM = "https://acme.example/team"
WRONG = "linkedin.com/in/janedoe"
RIGHT = "https://www.linkedin.com/in/jane-doe-acme"


def ident(kind: IdentifierKind, raw: str) -> Identifier:
    found = normalize(kind, raw)
    assert found is not None
    return found


def found_jane(**fields: object) -> FoundPerson:
    values: dict[str, object] = {
        "name": "Jane Doe",
        "title": "VP Engineering",
        "role": "other",
        "linkedin": RIGHT,
        "email": None,
        "source_url": TEAM,
        "quote": "Jane Doe, VP Engineering",
        "location": SourcedFact(value="Berlin", source_url=TEAM, quote="Jane Doe, Berlin"),
    }
    return FoundPerson.model_validate(values | fields)


def check(verdict: str, quote: str = "Jane Doe - Nurse at Charite") -> IdentifierCheck:
    return IdentifierCheck.model_validate(
        {
            "kind": "linkedin",
            "value": WRONG,
            "verdict": verdict,
            "source_url": "https://www.linkedin.com/in/janedoe" if quote else None,
            "quote": quote,
        }
    )


def findings(**fields: object) -> EntityFindings:
    values: dict[str, object] = {
        "company": None,
        "people": [found_jane()],
        "checks": [],
        "concerns": [],
    }
    return EntityFindings.model_validate(values | fields)


def values(entity: Entity) -> dict[str, object]:
    return {k: FieldValue.model_validate(v).value for k, v in entity.profile.items()}


def identifiers(session: Session, entity: Entity) -> set[tuple[str, str]]:
    rows = session.execute(
        select(EntityIdentifier.kind, EntityIdentifier.value).where(
            EntityIdentifier.entity_id == entity.id
        )
    )
    return {(kind.value, value) for kind, value in rows}


@pytest.fixture
def registry(session: Session) -> EntityRegistry:
    return EntityRegistry(session)


@pytest.fixture
def jane(registry: EntityRegistry) -> Entity:
    return registry.observe(
        Observation(
            kind=EntityKind.PERSON,
            name="Jane Doe",
            identifiers=[ident(IdentifierKind.LINKEDIN, WRONG)],
            fields={"title": FieldValue(value="Engineer", source_url=TEAM)},
        )
    )


@pytest.fixture
def context(project: Project, run: Run) -> IngestContext:
    return IngestContext(project_id=project.id, run_id=run.id, item_id=None)


class TestInput:
    def test_the_agent_sees_what_is_known_rejected_and_where_she_works(
        self, session: Session, registry: EntityRegistry, jane: Entity
    ) -> None:
        acme = registry.observe(Observation(kind=EntityKind.COMPANY, name="Acme GmbH"))
        registry.relate(RelationInput(from_id=jane.id, to_id=acme.id, type="works_at"))
        stale = ident(IdentifierKind.LINKEDIN, "linkedin.com/in/jane-d-old")
        second = ident(IdentifierKind.LINKEDIN, "linkedin.com/in/jane-doe-2")
        registry.attach(jane, [stale, second])
        registry.reject(jane, stale, "https://example.org", "another Jane")

        request = research_input(session, jane, "the LinkedIn is wrong")

        assert request.kind == "person"
        assert request.known == {"title": "Engineer"}
        # Both profiles are shown, so the wrong one of two can be checked.
        assert [(i.kind, i.value) for i in request.identifiers] == [
            ("linkedin", "linkedin.com/in/jane-doe-2"),
            ("linkedin", WRONG),
        ]
        assert request.employers == ["Acme GmbH"]
        assert request.rejected == ["linkedin.com/in/jane-d-old"]
        assert request.note == "the LinkedIn is wrong"


def test_a_linkedin_without_their_name_is_not_kept() -> None:
    accepted = accept_findings(findings(people=[found_jane(linkedin="linkedin.com/in/jd4521")]))

    assert accepted.people[0].linkedin is None
    assert "jd4521" in accepted.concerns[0]
    assert accept_findings(findings()).people[0].linkedin == RIGHT


def test_people_whose_quote_lacks_their_name_are_dropped() -> None:
    accepted = accept_findings(findings(people=[found_jane(quote="Our VP of Engineering")]))

    assert accepted.people == []
    assert accepted.concerns


class TestPerson:
    def test_a_checked_wrong_linkedin_is_replaced(
        self, session: Session, jane: Entity, context: IngestContext
    ) -> None:
        report = EntityUpdate(session).apply(
            jane, findings(checks=[check("someone_else")]), context
        )

        assert identifiers(session, jane) == {("linkedin", "linkedin.com/in/jane-doe-acme")}
        assert report.removed == [f"linkedin {WRONG}"]
        assert values(jane) == {
            "title": "VP Engineering",
            "location": "Berlin",
        }

    @pytest.mark.parametrize(
        "unproven",
        [
            check("unclear", quote=""),
            check("someone_else", quote=""),
            check("someone_else", quote="A nurse at Charite"),
            check("matches"),
        ],
        ids=["unclear", "no-quote", "quote-without-her-name", "matches"],
    )
    def test_an_unproven_verdict_keeps_both_profiles(
        self, session: Session, jane: Entity, context: IngestContext, unproven: IdentifierCheck
    ) -> None:
        report = EntityUpdate(session).apply(jane, findings(checks=[unproven]), context)

        assert identifiers(session, jane) == {
            ("linkedin", WRONG),
            ("linkedin", "linkedin.com/in/jane-doe-acme"),
        }
        assert report.removed == []

    def test_her_employer_is_registered_and_linked(
        self, session: Session, jane: Entity, context: IngestContext
    ) -> None:
        EntityUpdate(session).apply(jane, findings(company=company()), context)

        employer = session.scalars(select(Relation).where(Relation.from_id == jane.id)).one()
        acme = session.get(Entity, employer.to_id)
        assert acme is not None
        assert (acme.name, employer.type) == ("Acme GmbH", "works_at")
        assert ("name_at_company", "jane doe@acme.example") in identifiers(session, jane)

    def test_her_employer_is_reused_when_it_has_no_site(
        self, session: Session, jane: Entity, context: IngestContext
    ) -> None:
        unsited = company(domain=None, linkedin=None)
        for _ in range(2):
            EntityUpdate(session).apply(jane, findings(company=unsited), context)

        companies = session.scalars(select(Entity).where(Entity.kind == EntityKind.COMPANY))
        assert [c.name for c in companies] == ["Acme GmbH"]
        assert len(session.scalars(select(Relation)).all()) == 1

    @pytest.mark.parametrize(
        ("name", "quote"),
        [("John Roe", "John Roe, CTO"), ("Jane", "Jane, VP Engineering")],
        ids=["other-person", "first-name-only"],
    )
    def test_someone_else_found_leaves_her_as_she_was(
        self, session: Session, jane: Entity, context: IngestContext, name: str, quote: str
    ) -> None:
        found = findings(company=company(), people=[found_jane(name=name, quote=quote)])

        report = EntityUpdate(session).apply(jane, found, context)

        assert identifiers(session, jane) == {("linkedin", WRONG)}
        assert report.notes == ["The search didn't confirm Jane Doe on any page."]
        # Her employer is only written once she is confirmed.
        assert session.scalars(select(Entity).where(Entity.kind == EntityKind.COMPANY)).all() == []


class TestCompany:
    @pytest.fixture
    def acme(self, registry: EntityRegistry) -> Entity:
        return registry.observe(
            Observation(
                kind=EntityKind.COMPANY,
                name="Acme",
                identifiers=[ident(IdentifierKind.DOMAIN, "acme-robotics.com")],
                fields={"domain": FieldValue(value="acme-robotics.com", source_url=TEAM)},
            )
        )

    def wrong_site(self) -> IdentifierCheck:
        return IdentifierCheck(
            kind="domain",
            value="acme-robotics.com",
            verdict="someone_else",
            source_url="https://acme-robotics.com",
            quote="Acme Robotics, Ohio",
        )

    def test_a_wrong_site_is_replaced_and_key_people_linked(
        self, session: Session, acme: Entity, context: IngestContext
    ) -> None:
        found = findings(company=company(), checks=[self.wrong_site()])

        report = EntityUpdate(session).apply(acme, found, context)

        assert identifiers(session, acme) == {
            ("domain", "acme.example"),
            ("linkedin", "linkedin.com/company/acme-gmbh"),
        }
        assert values(acme)["sector"] == "Logistics software"
        assert values(acme)["domain"] == "acme.example"
        staff = session.scalars(select(Relation).where(Relation.to_id == acme.id)).one()
        assert (staff.type, staff.detail) == ("works_at", "VP Engineering")
        assert report.people == 1

    def test_a_site_judged_from_another_site_stays(
        self, session: Session, acme: Entity, context: IngestContext
    ) -> None:
        hearsay = self.wrong_site().model_copy(update={"source_url": "https://blog.example/x"})

        report = EntityUpdate(session).apply(acme, findings(checks=[hearsay]), context)

        assert identifiers(session, acme) == {("domain", "acme-robotics.com")}
        assert report.removed == []
        assert report.notes[0].startswith("Kept domain acme-robotics.com")

    def test_a_removed_site_leaves_the_profile_too(
        self, session: Session, acme: Entity, context: IngestContext
    ) -> None:
        EntityUpdate(session).apply(acme, findings(checks=[self.wrong_site()]), context)

        assert identifiers(session, acme) == set()
        assert "domain" not in acme.profile
        request = research_input(session, acme, None)
        assert (request.known, request.identifiers) == ({}, [])
        assert request.rejected == ["acme-robotics.com"]

    @pytest.mark.parametrize("name", ["Globex", "Acme Bank"])
    def test_a_different_company_and_its_people_are_not_written_onto_it(
        self, session: Session, acme: Entity, context: IngestContext, name: str
    ) -> None:
        report = EntityUpdate(session).apply(acme, findings(company=company(name=name)), context)

        assert identifiers(session, acme) == {("domain", "acme-robotics.com")}
        assert name in report.notes[0]
        assert session.scalars(select(Relation)).all() == []
        assert report.people == 0
