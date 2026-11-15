"""Write a lead's company and people into the registry, for biz, job and freelance alike."""

import logging
from urllib.parse import urlsplit

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from mozg_outreach.domain import EntityKind, FieldValue, IdentifierKind, SourceKind
from mozg_outreach.registry.identifiers import (
    Identifier,
    looks_personal,
    name_key,
    normalize,
    person_at_company,
)
from mozg_outreach.registry.registry import (
    EntityRegistry,
    Observation,
    RelationInput,
    SightingInput,
)
from mozg_outreach.registry.suggestions import MergeSuggester
from mozg_outreach.storage.models import Entity, EntityIdentifier, ItemLink

logger = logging.getLogger(__name__)

SUBJECT, CONTACT = "subject", "contact"


class IngestContext(BaseModel):
    project_id: int
    run_id: int | None
    item_id: int


class SeenEmail(BaseModel):
    address: str
    source_url: str
    # Only a published address identifies someone; a pattern guess stays a labelled field.
    published: bool


class PersonSeen(BaseModel):
    name: str = Field(min_length=1)
    title: str | None
    source_url: str
    quote: str = ""
    linkedin: str | None = None
    email: SeenEmail | None = None
    telegram: str | None = Field(default=None, description="Username, which identifies them.")


class CompanySeen(BaseModel):
    name: str = Field(min_length=1)
    identifiers: list[Identifier]
    fields: dict[str, FieldValue]
    sightings: list[SightingInput]


class LeadIngest:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._registry = EntityRegistry(session)
        self._suggester = MergeSuggester(session)

    def company(self, seen: CompanySeen, context: IngestContext) -> Entity:
        """The lead's company becomes its subject, with every page it was seen on."""
        reused = None if seen.identifiers else self._linked(context, SUBJECT, seen.name)
        observation = Observation(
            kind=EntityKind.COMPANY,
            name=seen.name,
            identifiers=seen.identifiers,
            fields=seen.fields,
        )
        entity = self._registry.observe(observation) if reused is None else reused
        for sighting in seen.sightings:
            self._registry.add_sighting(entity, sighting)
        self._registry.link(context.item_id, entity, SUBJECT)
        self._suggester.suggest_for(entity)
        return entity

    def person(self, seen: PersonSeen, company: Entity | None, context: IngestContext) -> Entity:
        """A named person becomes the lead's contact, working at `company` when it is known."""
        email = seen.email
        identifiers = _valid(
            [
                person_at_company(seen.name, self.employer_key(company)) if company else None,
                normalize(IdentifierKind.LINKEDIN, seen.linkedin),
                normalize(IdentifierKind.TELEGRAM, seen.telegram),
                normalize(IdentifierKind.EMAIL, email.address)
                if email and email.published and looks_personal(email.address, seen.name)
                else None,
            ]
        )
        fields: dict[str, FieldValue] = {}
        if seen.title:
            fields["title"] = FieldValue(
                value=seen.title, source_url=seen.source_url, quote=seen.quote or None
            )
        if email:
            fields["email"] = FieldValue(
                value=email.address,
                source_url=email.source_url,
                confidence="unverified" if email.published else "guessed",
            )
        sighting = sighting_at(seen.source_url, seen.quote, context)
        reused = None if identifiers else self._linked(context, CONTACT, seen.name)
        if reused is None:
            entity = self._registry.observe(
                Observation(
                    kind=EntityKind.PERSON,
                    name=seen.name,
                    identifiers=identifiers,
                    fields=fields,
                    sighting=sighting,
                )
            )
        else:
            entity = reused
            self._registry.add_sighting(entity, sighting)
        if company is not None:
            self._registry.relate(
                RelationInput(
                    from_id=entity.id,
                    to_id=company.id,
                    type="works_at",
                    detail=seen.title,
                    source_url=seen.source_url,
                    quote=seen.quote,
                )
            )
        self._registry.link(context.item_id, entity, CONTACT)
        self._suggester.suggest_for(entity)
        return entity

    def subject(self, context: IngestContext) -> Entity | None:
        return self._session.scalar(
            select(Entity)
            .join(ItemLink, ItemLink.entity_id == Entity.id)
            .where(ItemLink.item_id == context.item_id, ItemLink.role == SUBJECT)
        )

    def attach(self, entity: Entity, identifiers: list[Identifier | None]) -> None:
        self._registry.attach(entity, _valid(identifiers))

    def employer_key(self, company: Entity) -> str:
        domain = self._session.scalar(
            select(EntityIdentifier.value).where(
                EntityIdentifier.entity_id == company.id,
                EntityIdentifier.kind == IdentifierKind.DOMAIN,
            )
        )
        return domain or f"entity-{company.id}"

    def _linked(self, context: IngestContext, role: str, name: str) -> Entity | None:
        """An entity this item already links under `role` by the same name, so re-runs reuse it."""
        linked = self._session.scalars(
            select(Entity)
            .join(ItemLink, ItemLink.entity_id == Entity.id)
            .where(ItemLink.item_id == context.item_id, ItemLink.role == role)
        )
        return next((e for e in linked if name_key(e.name) == name_key(name)), None)


def sighting_at(
    url: str, quote: str, context: IngestContext, kind: SourceKind | None = None
) -> SightingInput:
    return SightingInput(
        source_kind=kind or url_kind(url),
        source_url=url,
        quote=quote,
        project_id=context.project_id,
        run_id=context.run_id,
    )


def url_kind(url: str) -> SourceKind:
    host = urlsplit(url).hostname or ""
    return SourceKind.TELEGRAM if host in {"t.me", "telegram.me"} else SourceKind.WEB


def _valid(identifiers: list[Identifier | None]) -> list[Identifier]:
    return [i for i in identifiers if i is not None]
