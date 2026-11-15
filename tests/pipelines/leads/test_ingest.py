import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mozg_outreach.domain import EntityKind, FieldValue, IdentifierKind, ProjectMode, SourceKind
from mozg_outreach.pipelines.job.models import JobContacts, Listing, NamedContact, TelegramContact
from mozg_outreach.pipelines.job.search import to_candidate
from mozg_outreach.pipelines.leads.ingest import LeadRegistryIngest, listing_kind
from mozg_outreach.registry.ingest import IngestContext
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
from tests.pipelines.job.conftest import listing

from .conftest import CAREERS, POSTING, company, identity, person


def context_for(session: Session, project: Project, run: Run, lead: Listing) -> IngestContext:
    item = ItemRepository(session).upsert(project.id, run.id, to_candidate(lead))
    return IngestContext(project_id=project.id, run_id=run.id, item_id=item.id)


@pytest.fixture
def lead() -> Listing:
    return listing(url=POSTING, key=POSTING, sources=["greenhouse"], merged_urls=[CAREERS])


@pytest.fixture
def context(session: Session, project: Project, run: Run, lead: Listing) -> IngestContext:
    return context_for(session, project, run, lead)


def entities(session: Session, kind: EntityKind) -> list[Entity]:
    return list(session.scalars(select(Entity).where(Entity.kind == kind)))


def links(session: Session, item_id: int) -> set[tuple[str, str]]:
    rows = session.execute(
        select(ItemLink.role, Entity.name)
        .join(Entity, Entity.id == ItemLink.entity_id)
        .where(ItemLink.item_id == item_id)
    )
    return {(role, name) for role, name in rows}


class TestIdentity:
    def test_confirmed_company_is_the_subject_and_people_work_there(
        self, session: Session, lead: Listing, context: IngestContext
    ) -> None:
        counts = LeadRegistryIngest(session).identity(
            lead, identity(company(), [person()]), context
        )

        assert (counts.companies, counts.people) == (1, 1)
        assert links(session, context.item_id) == {
            ("subject", "Acme GmbH"),
            ("contact", "Jane Doe"),
        }
        [acme] = entities(session, EntityKind.COMPANY)
        ids = set(session.execute(select(EntityIdentifier.kind, EntityIdentifier.value)).all())
        assert (IdentifierKind.DOMAIN, "acme.example") in ids
        assert (IdentifierKind.LINKEDIN, "linkedin.com/company/acme-gmbh") in ids
        assert FieldValue.model_validate(acme.profile["sector"]).source_url == CAREERS
        seen = [(s.source_url, s.source_kind, s.quote) for s in session.scalars(select(Sighting))]
        company_pages = [s for s in seen if s[0] in {POSTING, CAREERS} and s[2] != person().quote]
        assert sorted(company_pages) == [
            (CAREERS, SourceKind.FEED, company().quote),
            (POSTING, SourceKind.FEED, ""),
        ], "one sighting per page; the confirming page carries the quote"
        [works] = session.scalars(select(Relation))
        assert (works.type, works.to_id) == ("works_at", acme.id)

    def test_no_company_means_no_company_link(
        self, session: Session, lead: Listing, context: IngestContext
    ) -> None:
        LeadRegistryIngest(session).identity(lead, identity(None, [person()]), context)

        assert entities(session, EntityKind.COMPANY) == []
        assert links(session, context.item_id) == {("contact", "Jane Doe")}
        assert session.scalars(select(Relation)).all() == []

    def test_a_rerun_reuses_entities_without_identifiers(
        self, session: Session, lead: Listing, context: IngestContext
    ) -> None:
        bare = company(domain=None, linkedin=None)
        ingest = LeadRegistryIngest(session)

        ingest.identity(lead, identity(bare, [person()]), context)
        ingest.identity(lead, identity(bare, [person()]), context)

        assert len(entities(session, EntityKind.COMPANY)) == 1
        assert len(entities(session, EntityKind.PERSON)) == 1

    def test_the_same_domain_in_another_project_is_one_company(
        self, session: Session, lead: Listing, context: IngestContext, run: Run
    ) -> None:
        other = Project(name="Second search", mode=ProjectMode.JOB, brief=None)
        session.add(other)
        session.flush()
        again = context_for(session, other, run, lead)

        LeadRegistryIngest(session).identity(lead, identity(company()), context)
        LeadRegistryIngest(session).identity(lead, identity(company()), again)

        [acme] = entities(session, EntityKind.COMPANY)
        assert links(session, again.item_id) == {("subject", acme.name)}


def test_listing_kind_follows_its_sources(lead: Listing) -> None:
    assert listing_kind(lead) == SourceKind.FEED
    assert listing_kind(listing(sources=["Remotive board"])) == SourceKind.WEB
    assert listing_kind(listing(url="https://t.me/jobs/42", sources=["x"])) == SourceKind.TELEGRAM


class TestContacts:
    @staticmethod
    def found(**fields: object) -> JobContacts:
        values: dict[str, object] = {
            "named_contact": NamedContact(
                name="Ann Lee",
                title="Recruiter",
                linkedin=None,
                email="ann@acme.example",
                source_url=POSTING,
            ),
            "careers_email": None,
            "careers_email_source": None,
            "linkedin_company": "https://www.linkedin.com/company/acme-gmbh",
            "pattern_guesses": [],
            "apply_url": None,
            "concerns": [],
        }
        return JobContacts.model_validate(values | fields)

    def test_contact_joins_the_confirmed_company(
        self, session: Session, lead: Listing, context: IngestContext
    ) -> None:
        ingest = LeadRegistryIngest(session)
        ingest.identity(lead, identity(company(linkedin=None)), context)

        ann = ingest.contacts(self.found(), context)

        assert ann is not None
        [acme] = entities(session, EntityKind.COMPANY)
        [works] = session.scalars(select(Relation))
        assert (works.from_id, works.to_id) == (ann.id, acme.id)
        linkedin = session.scalar(
            select(EntityIdentifier.entity_id).where(
                EntityIdentifier.kind == IdentifierKind.LINKEDIN
            )
        )
        assert linkedin == acme.id

    def test_contact_without_a_company_links_to_the_lead_only(
        self, session: Session, context: IngestContext
    ) -> None:
        LeadRegistryIngest(session).contacts(self.found(), context)

        assert entities(session, EntityKind.COMPANY) == []
        assert links(session, context.item_id) == {("contact", "Ann Lee")}


class TestTelegramContact:
    @pytest.fixture
    def post(self, lead: Listing) -> Listing:
        contact = TelegramContact(handle="hr_anna", name="Anna", quote="Write to Anna: @hr_anna")
        return lead.model_copy(update={"telegram_contact": contact})

    def test_contact_works_at_the_confirmed_company(
        self, session: Session, post: Listing, context: IngestContext
    ) -> None:
        ingest = LeadRegistryIngest(session)
        ingest.identity(post, identity(company()), context)

        anna = ingest.telegram_contact(post, context)

        assert anna is not None
        [acme] = entities(session, EntityKind.COMPANY)
        [works] = session.scalars(select(Relation))
        assert (works.from_id, works.to_id) == (anna.id, acme.id)

    def test_contact_without_a_confirmed_company_links_to_the_lead_only(
        self, session: Session, post: Listing, context: IngestContext
    ) -> None:
        ingest = LeadRegistryIngest(session)
        ingest.identity(post, identity(None), context)

        ingest.telegram_contact(post, context)

        assert list(session.scalars(select(Relation))) == []
        assert links(session, context.item_id) == {("contact", "Anna")}
