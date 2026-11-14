"""Write biz pipeline results into the registry: the company, its people and their sources."""

import logging

from sqlalchemy.orm import Session

from mozg_outreach.domain import IdentifierKind, ItemCandidate
from mozg_outreach.pipelines.biz.enrichment_models import CompanyDossier, EmailContact
from mozg_outreach.registry.identifiers import normalize
from mozg_outreach.registry.ingest import (
    CompanySeen,
    IngestContext,
    LeadIngest,
    PersonSeen,
    SeenEmail,
    sighting_at,
)
from mozg_outreach.storage.models import Entity

logger = logging.getLogger(__name__)

COMPANY_FIELDS = ("domain", "sector", "location", "founded", "description")


class BizRegistryIngest:
    def __init__(self, session: Session) -> None:
        self._leads = LeadIngest(session)

    def company(self, candidate: ItemCandidate, context: IngestContext) -> Entity:
        """The scouted company becomes the lead's subject, with every page it was found on."""
        fields = {k: v for k, v in candidate.fields.items() if k in COMPANY_FIELDS}
        domain = candidate.fields.get("domain")
        raw_domain = domain.value if domain and isinstance(domain.value, str) else None
        found = normalize(IdentifierKind.DOMAIN, raw_domain)
        quote = next((v.quote for v in fields.values() if v.quote), "") or ""
        urls = candidate.fields.get("source_urls")
        pages = urls.value if urls and isinstance(urls.value, list) else []
        return self._leads.company(
            CompanySeen(
                name=candidate.title,
                identifiers=[found] if found else [],
                fields=fields,
                sightings=[sighting_at(url, quote, context) for url in pages],
            ),
            context,
        )

    def dossier(
        self, company: Entity, dossier: CompanyDossier, context: IngestContext
    ) -> list[Entity]:
        """People from enrichment become registry entries that work at the company."""
        people: list[PersonSeen] = []
        if dossier.contacts:
            people += [
                PersonSeen(
                    name=p.name,
                    title=p.title,
                    source_url=p.source_url,
                    linkedin=p.linkedin,
                    email=_seen(p.email),
                )
                for p in dossier.contacts.key_people
            ]
            linkedin = normalize(IdentifierKind.LINKEDIN, dossier.contacts.social.linkedin_company)
            self._leads.attach(company, [linkedin])
        if dossier.tech:
            people += [
                PersonSeen(name=p.name, title=p.title, source_url=p.source_url)
                for p in dossier.tech.tech_decision_makers
            ]
        entities = [self._leads.person(person, company, context) for person in people]
        logger.info("registry: %d people ingested for %s", len(people), company.name)
        return list({e.id: e for e in entities}.values())


def _seen(email: EmailContact | None) -> SeenEmail | None:
    if email is None:
        return None
    return SeenEmail(
        address=email.address, source_url=email.source_url, published=email.kind == "published"
    )
