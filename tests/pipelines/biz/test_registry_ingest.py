import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mozg_outreach.domain import EntityKind, FieldValue, IdentifierKind, ItemCandidate
from mozg_outreach.pipelines.biz.discovery import dedup_companies
from mozg_outreach.pipelines.biz.enrichment_models import (
    CompanyDossier,
    ContactsResult,
    EmailContact,
    TechResult,
)
from mozg_outreach.pipelines.biz.models import ScoutResult
from mozg_outreach.pipelines.biz.registry_ingest import BizRegistryIngest, IngestContext
from mozg_outreach.storage.models import (
    Entity,
    EntityIdentifier,
    ItemLink,
    Project,
    Relation,
    Run,
    Sighting,
)
from mozg_outreach.storage.repositories import ItemRepository
from tests.pipelines.biz.conftest import load_fixture


@pytest.fixture
def katana() -> ItemCandidate:
    return dedup_companies(load_fixture("scout_results", list[ScoutResult])[0].companies)[0]


@pytest.fixture
def dossier() -> CompanyDossier:
    contacts = load_fixture("enrichment/contacts", ContactsResult)
    guessed = EmailContact(
        address="jane@katanamrp.com",
        kind="pattern_guess",
        source_url="https://katanamrp.com/about/",
    )
    contacts.key_people[0].email = guessed
    return CompanyDossier(contacts=contacts, tech=load_fixture("enrichment/tech", TechResult))


@pytest.fixture
def context(session: Session, project: Project, run: Run, katana: ItemCandidate) -> IngestContext:
    item = ItemRepository(session).upsert(project.id, run.id, katana)
    return IngestContext(project_id=project.id, run_id=run.id, item_id=item.id)


def ingest(
    session: Session, katana: ItemCandidate, dossier: CompanyDossier, context: IngestContext
) -> None:
    ingester = BizRegistryIngest(session)
    company = ingester.company(katana, context)
    ingester.dossier(company, dossier, context)


def rows[TRow](session: Session, model: type[TRow]) -> list[TRow]:
    return list(session.scalars(select(model)))


def test_lead_links_company_and_people_with_their_sources(
    session: Session, katana: ItemCandidate, dossier: CompanyDossier, context: IngestContext
) -> None:
    ingest(session, katana, dossier, context)

    entities = {e.kind: e for e in rows(session, Entity)}
    assert len(rows(session, Entity)) == 2
    company, jane = entities[EntityKind.COMPANY], entities[EntityKind.PERSON]
    identifiers = {(i.kind, i.value) for i in rows(session, EntityIdentifier)}
    assert identifiers == {
        (IdentifierKind.DOMAIN, "katanamrp.com"),
        (IdentifierKind.LINKEDIN, "linkedin.com/company/katanamrp"),
        (IdentifierKind.NAME_AT_COMPANY, "jane example@katanamrp.com"),
    }
    email = FieldValue.model_validate(jane.profile["email"])
    assert (email.value, email.confidence) == ("jane@katanamrp.com", "guessed")
    [works_at] = rows(session, Relation)
    assert (works_at.from_id, works_at.to_id, works_at.detail) == (jane.id, company.id, "CTO")
    links = {(link.entity_id, link.role) for link in rows(session, ItemLink)}
    assert links == {(company.id, "subject"), (jane.id, "contact")}
    company_urls = {s.source_url for s in rows(session, Sighting) if s.entity_id == company.id}
    scouted = katana.fields["source_urls"].value
    assert isinstance(scouted, list)
    assert company_urls == set(scouted)
    assert all(s.project_id == context.project_id for s in rows(session, Sighting))


def test_ingesting_twice_changes_nothing(
    session: Session, katana: ItemCandidate, dossier: CompanyDossier, context: IngestContext
) -> None:
    ingest(session, katana, dossier, context)
    before = [
        len(rows(session, m)) for m in (Entity, EntityIdentifier, Sighting, Relation, ItemLink)
    ]
    ingest(session, katana, dossier, context)
    after = [
        len(rows(session, m)) for m in (Entity, EntityIdentifier, Sighting, Relation, ItemLink)
    ]
    assert after == before
