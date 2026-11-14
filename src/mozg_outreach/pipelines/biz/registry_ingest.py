"""Write biz pipeline results into the registry: the company, its people and their sources."""

import logging

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from mozg_outreach.domain import (
    EntityKind,
    FieldValue,
    IdentifierKind,
    ItemCandidate,
    SourceKind,
)
from mozg_outreach.pipelines.biz.enrichment_models import CompanyDossier, EmailContact
from mozg_outreach.registry.identifiers import Identifier, normalize, person_at_company
from mozg_outreach.registry.registry import (
    EntityRegistry,
    Observation,
    RelationInput,
    SightingInput,
)
from mozg_outreach.registry.suggestions import MergeSuggester
from mozg_outreach.storage.models import Entity, EntityIdentifier

logger = logging.getLogger(__name__)

COMPANY_FIELDS = ("domain", "sector", "location", "founded", "description")


class IngestContext(BaseModel):
    project_id: int
    run_id: int | None
    item_id: int


class PersonSeen(BaseModel):
    name: str
    title: str
    source_url: str
    linkedin: str | None = None
    email: EmailContact | None = None


class BizRegistryIngest:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._registry = EntityRegistry(session)
        self._suggester = MergeSuggester(session)

    def company(self, candidate: ItemCandidate, context: IngestContext) -> Entity:
        """The scouted company becomes the lead's subject, with every page it was found on."""
        fields = {k: v for k, v in candidate.fields.items() if k in COMPANY_FIELDS}
        domain = candidate.fields.get("domain")
        raw_domain = domain.value if domain and isinstance(domain.value, str) else None
        identifiers = _valid([normalize(IdentifierKind.DOMAIN, raw_domain)])
        entity = self._registry.observe(
            Observation(
                kind=EntityKind.COMPANY,
                name=candidate.title,
                identifiers=identifiers,
                fields=fields,
            )
        )
        quote = next((v.quote for v in fields.values() if v.quote), "") or ""
        urls = candidate.fields.get("source_urls")
        for url in urls.value if urls and isinstance(urls.value, list) else []:
            self._registry.add_sighting(entity, self._sighting(url, quote, context))
        self._registry.link(context.item_id, entity, "subject")
        self._suggester.suggest_for(entity)
        return entity

    def dossier(
        self, company: Entity, dossier: CompanyDossier, context: IngestContext
    ) -> list[Entity]:
        """People from enrichment become registry entries that work at the company."""
        people: list[PersonSeen] = []
        if dossier.contacts:
            people += [
                PersonSeen.model_validate(p, from_attributes=True)
                for p in dossier.contacts.key_people
            ]
            linkedin = normalize(IdentifierKind.LINKEDIN, dossier.contacts.social.linkedin_company)
            self._registry.attach(company, _valid([linkedin]))
        if dossier.tech:
            people += [
                PersonSeen(name=p.name, title=p.title, source_url=p.source_url)
                for p in dossier.tech.tech_decision_makers
            ]
        employer = self._employer_key(company)
        entities = [self._person(person, company, employer, context) for person in people]
        logger.info("registry: %d people ingested for %s", len(people), company.name)
        return list({e.id: e for e in entities}.values())

    def _person(
        self, person: PersonSeen, company: Entity, employer: str, context: IngestContext
    ) -> Entity:
        email = person.email
        identifiers = _valid(
            [
                person_at_company(person.name, employer),
                normalize(IdentifierKind.LINKEDIN, person.linkedin),
                # A guessed address identifies nobody; it stays a labelled profile field.
                normalize(IdentifierKind.EMAIL, email.address)
                if email and email.kind == "published"
                else None,
            ]
        )
        fields = {"title": FieldValue(value=person.title, source_url=person.source_url)}
        if email:
            fields["email"] = FieldValue(
                value=email.address,
                source_url=email.source_url,
                confidence="unverified" if email.kind == "published" else "guessed",
            )
        entity = self._registry.observe(
            Observation(
                kind=EntityKind.PERSON,
                name=person.name,
                identifiers=identifiers,
                fields=fields,
                sighting=self._sighting(person.source_url, "", context),
            )
        )
        self._registry.relate(
            RelationInput(
                from_id=entity.id,
                to_id=company.id,
                type="works_at",
                detail=person.title,
                source_url=person.source_url,
            )
        )
        self._registry.link(context.item_id, entity, "contact")
        self._suggester.suggest_for(entity)
        return entity

    def _employer_key(self, company: Entity) -> str:
        domain = self._session.scalar(
            select(EntityIdentifier.value).where(
                EntityIdentifier.entity_id == company.id,
                EntityIdentifier.kind == IdentifierKind.DOMAIN,
            )
        )
        return domain or f"entity-{company.id}"

    @staticmethod
    def _sighting(url: str, quote: str, context: IngestContext) -> SightingInput:
        return SightingInput(
            source_kind=SourceKind.WEB,
            source_url=url,
            quote=quote,
            project_id=context.project_id,
            run_id=context.run_id,
        )


def _valid(identifiers: list[Identifier | None]) -> list[Identifier]:
    return [i for i in identifiers if i is not None]
