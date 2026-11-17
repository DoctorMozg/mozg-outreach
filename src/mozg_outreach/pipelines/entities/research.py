"""One more round of searching on a registry person or company, checked and applied in Python."""

import logging

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from mozg_outreach.domain import EntityKind, FieldValue, IdentifierKind
from mozg_outreach.pipelines.entities.models import EntityFindings, EntityResearchInput
from mozg_outreach.pipelines.leads.identify import accept_company, accept_person
from mozg_outreach.pipelines.leads.ingest import company_seen, person_seen
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.registry.identifiers import name_key, normalize
from mozg_outreach.registry.ingest import IngestContext, LeadIngest, person_fields, sighting_at
from mozg_outreach.registry.registry import EntityRegistry
from mozg_outreach.registry.suggestions import MergeSuggester
from mozg_outreach.storage.models import Entity, EntityIdentifier, Relation, Sighting

logger = logging.getLogger(__name__)

MAX_SOURCES = 10
CHECKED_KINDS = {"linkedin": IdentifierKind.LINKEDIN, "domain": IdentifierKind.DOMAIN}
# Exact identifiers worth showing the agent; name@employer is an internal merge key.
SHOWN_KINDS = (IdentifierKind.LINKEDIN, IdentifierKind.DOMAIN, IdentifierKind.EMAIL)


class ResearchReport(BaseModel):
    removed: list[str]
    people: int
    notes: list[str]


def research_input(session: Session, entity: Entity, note: str | None) -> EntityResearchInput:
    """What the registry knows about the entity, so the agent checks it and searches the rest."""
    profile = {k: FieldValue.model_validate(v) for k, v in entity.profile.items()}
    known = {k: str(v.value) for k, v in profile.items() if v.value not in (None, "", [])}
    for row in session.scalars(
        select(EntityIdentifier).where(
            EntityIdentifier.entity_id == entity.id, EntityIdentifier.kind.in_(SHOWN_KINDS)
        )
    ):
        known[row.kind.value] = row.value
    sources = session.scalars(
        select(Sighting.source_url)
        .where(Sighting.entity_id == entity.id)
        .order_by(Sighting.seen_at.desc())
    )
    pages = [url for url in dict.fromkeys(sources) if "t.me/" not in url][:MAX_SOURCES]
    person = entity.kind == EntityKind.PERSON
    return EntityResearchInput(
        kind="person" if person else "company",
        name=entity.name,
        known=known,
        employers=_works_at(session, entity, outgoing=True) if person else [],
        people=[] if person else _works_at(session, entity, outgoing=False),
        sources=pages,
        rejected=[i.value for i in EntityRegistry(session).rejected(entity.id)],
        note=note,
    )


class EntityResearcher:
    def __init__(self, steps: PipelineSteps) -> None:
        self._steps = steps

    async def find(self, request: EntityResearchInput) -> EntityFindings:
        found = await self._steps.run("shared", "entity-researcher", request, EntityFindings)
        return accept_findings(found)


def accept_findings(found: EntityFindings) -> EntityFindings:
    """The lead identifier's checks: a domain on its own page, names and emails in their quotes."""
    concerns = list(found.concerns)
    company = accept_company(found.company, concerns) if found.company else None
    people = [p for p in (accept_person(p, concerns) for p in found.people) if p is not None]
    return found.model_copy(update={"company": company, "people": people, "concerns": concerns})


class EntityUpdate:
    """Writes one search's findings onto the entity, the same way lead enrichment writes leads."""

    def __init__(self, session: Session) -> None:
        self._registry = EntityRegistry(session)
        self._ingest = LeadIngest(session)
        self._suggester = MergeSuggester(session)

    def apply(
        self, entity: Entity, findings: EntityFindings, context: IngestContext
    ) -> ResearchReport:
        report = ResearchReport(removed=[], people=0, notes=[])
        self._reject(entity, findings, report)
        if entity.kind == EntityKind.COMPANY:
            self._company(entity, findings, context, report)
        else:
            self._person(entity, findings, context, report)
        self._suggester.suggest_for(entity)
        logger.info(
            "researched %s %d: %d removed, %d people",
            entity.kind,
            entity.id,
            len(report.removed),
            report.people,
        )
        return report

    def _reject(self, entity: Entity, findings: EntityFindings, report: ResearchReport) -> None:
        """Only a verdict the agent quoted a page for takes an identifier off."""
        for check in findings.checks:
            if check.verdict != "someone_else" or not check.source_url or not check.quote.strip():
                continue
            identifier = normalize(CHECKED_KINDS[check.kind], check.value)
            if identifier and self._registry.reject(
                entity, identifier, check.source_url, check.quote
            ):
                report.removed.append(f"{check.kind} {identifier.value}")
                report.notes.append(f"Removed {check.kind} {identifier.value}: {check.quote}")

    def _company(
        self,
        entity: Entity,
        findings: EntityFindings,
        context: IngestContext,
        report: ResearchReport,
    ) -> None:
        found = findings.company
        if found is None or not _same_name(found.name, entity.name):
            other = f" ({found.name})" if found else ""
            report.notes.append(f"The search confirmed nothing new about {entity.name}{other}.")
        else:
            seen = company_seen(found, [sighting_at(found.source_url, found.quote, context)])
            self._registry.attach(entity, seen.identifiers)
            self._registry.refresh(entity, seen.fields)
            for sighting in seen.sightings:
                self._registry.add_sighting(entity, sighting)
        for person in findings.people:
            self._ingest.person(person_seen(person), entity, context)
            report.people += 1

    def _person(
        self,
        entity: Entity,
        findings: EntityFindings,
        context: IngestContext,
        report: ResearchReport,
    ) -> None:
        employer = None
        if findings.company is not None:
            sighting = sighting_at(findings.company.source_url, findings.company.quote, context)
            employer = self._ingest.company(company_seen(findings.company, [sighting]), context)
        found = next((p for p in findings.people if _same_name(p.name, entity.name)), None)
        if found is None:
            report.notes.append(f"The search didn't confirm {entity.name} on any page.")
            return
        seen = person_seen(found)
        self._registry.attach(entity, self._ingest.person_identifiers(seen, employer))
        fields = person_fields(seen)
        if found.location is not None:
            fact = found.location
            fields["location"] = FieldValue(
                value=fact.value, source_url=fact.source_url, quote=fact.quote
            )
        self._registry.refresh(entity, fields)
        self._registry.add_sighting(entity, sighting_at(seen.source_url, seen.quote, context))
        if employer is not None:
            self._ingest.works_at(entity, employer, seen)


def _works_at(session: Session, entity: Entity, *, outgoing: bool) -> list[str]:
    """A person's employers, or a company's people with their titles."""
    mine, other = (
        (Relation.from_id, Relation.to_id) if outgoing else (Relation.to_id, Relation.from_id)
    )
    rows = session.execute(
        select(Entity.name, Relation.detail)
        .join(Relation, other == Entity.id)
        .where(mine == entity.id, Relation.type == "works_at")
    )
    if outgoing:
        return list(dict.fromkeys(name for name, _ in rows))
    return list(dict.fromkeys(f"{name}, {title}" if title else name for name, title in rows))


def _same_name(found: str, known: str) -> bool:
    """The search may write the official form ("Acme GmbH") or add a middle name."""
    a, b = set(name_key(found).split()), set(name_key(known).split())
    return bool(a and b) and (a <= b or b <= a)
