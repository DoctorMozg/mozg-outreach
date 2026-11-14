"""Register job and freelance leads: the confirmed company as subject, named people as contacts."""

from pydantic import BaseModel
from sqlalchemy.orm import Session

from mozg_outreach.domain import FieldValue, IdentifierKind, SourceKind
from mozg_outreach.pipelines.job.models import JobContacts, Listing
from mozg_outreach.pipelines.leads.models import IdentifiedCompany, LeadIdentity, SourcedFact
from mozg_outreach.registry.identifiers import normalize
from mozg_outreach.registry.ingest import (
    CompanySeen,
    IngestContext,
    LeadIngest,
    PersonSeen,
    SeenEmail,
    sighting_at,
    url_kind,
)
from mozg_outreach.sources.factory import source_kinds
from mozg_outreach.storage.models import Entity


class IngestCounts(BaseModel):
    companies: int
    people: int


class LeadRegistryIngest:
    def __init__(self, session: Session) -> None:
        self._leads = LeadIngest(session)

    def identity(
        self, listing: Listing, identity: LeadIdentity, context: IngestContext
    ) -> IngestCounts:
        """No confirmed company means no company entity: people then link to the lead alone."""
        company = None
        if identity.company is not None:
            company = self._leads.company(_company(listing, identity.company, context), context)
        for person in identity.people:
            email = (
                SeenEmail(address=person.email, source_url=person.source_url, published=True)
                if person.email
                else None
            )
            seen = PersonSeen(
                name=person.name,
                title=person.title,
                source_url=person.source_url,
                quote=person.quote,
                linkedin=person.linkedin,
                email=email,
            )
            self._leads.person(seen, company, context)
        return IngestCounts(companies=int(company is not None), people=len(identity.people))

    def telegram_contact(self, listing: Listing, context: IngestContext) -> Entity | None:
        """A post's contact links to the vacancy only: Telegram recruiters often post for many
        employers, so the post shows no tie to the company."""
        contact = listing.telegram_contact
        if contact is None:
            return None
        seen = PersonSeen(
            name=contact.name or f"@{contact.handle}",
            title=None,
            source_url=listing.url,
            quote=contact.quote,
            telegram=contact.handle,
        )
        return self._leads.person(seen, None, context)

    def contacts(self, contacts: JobContacts, context: IngestContext) -> Entity | None:
        """The contact finder's named person joins the lead's company, if one was confirmed."""
        company = self._leads.subject(context)
        if company is not None:
            self._leads.attach(
                company, [normalize(IdentifierKind.LINKEDIN, contacts.linkedin_company)]
            )
        found = contacts.named_contact
        if found is None:
            return None
        email = (
            SeenEmail(address=found.email, source_url=found.source_url, published=True)
            if found.email
            else None
        )
        seen = PersonSeen(
            name=found.name,
            title=found.title,
            source_url=found.source_url,
            linkedin=found.linkedin,
            email=email,
        )
        return self._leads.person(seen, company, context)


def _company(listing: Listing, company: IdentifiedCompany, context: IngestContext) -> CompanySeen:
    facts: dict[str, SourcedFact | None] = {
        "domain": company.domain,
        "sector": company.sector,
        "location": company.location,
        "description": company.description,
    }
    fields = {
        name: FieldValue(value=fact.value, source_url=fact.source_url, quote=fact.quote)
        for name, fact in facts.items()
        if fact is not None
    }
    identifiers = [
        normalize(IdentifierKind.DOMAIN, company.domain.value if company.domain else None),
        normalize(IdentifierKind.LINKEDIN, company.linkedin.value if company.linkedin else None),
    ]
    # One sighting per page: listing pages keep the listing's source kind, and the page that
    # confirms the company carries its quote.
    listing_urls = [listing.url, *listing.merged_urls]
    kind = listing_kind(listing)
    pages = dict.fromkeys(listing_urls, "")
    pages[company.source_url] = company.quote
    sightings = [
        sighting_at(url, quote, context, kind if url in listing_urls else None)
        for url, quote in pages.items()
    ]
    return CompanySeen(
        name=company.name,
        identifiers=[i for i in identifiers if i is not None],
        fields=fields,
        sightings=sightings,
    )


def listing_kind(listing: Listing) -> SourceKind:
    """Where the listing came from: a Telegram channel, a feed adapter or an agent's web search."""
    if url_kind(listing.url) == SourceKind.TELEGRAM or "telegram" in listing.sources:
        return SourceKind.TELEGRAM
    if set(listing.sources) & set(source_kinds()):
        return SourceKind.FEED
    return SourceKind.WEB
