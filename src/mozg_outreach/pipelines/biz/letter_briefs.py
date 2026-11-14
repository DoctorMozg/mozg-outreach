"""Turn an enriched company into letter inputs: who to write to, how, and with which facts."""

from typing import Literal

from pydantic import BaseModel, Field

from mozg_outreach.domain import FieldValue
from mozg_outreach.letters.models import Angle, Hook
from mozg_outreach.naturalize.models import Channel
from mozg_outreach.pipelines.biz.enrichment_models import CompanyDossier, EmailContact
from mozg_outreach.pipelines.biz.models import OutreachAngle
from mozg_outreach.registry.identifiers import name_key

MAX_CONTACTS = 5
MAX_HOOKS = 12
AddressConfidence = Literal["published", "guessed", "linkedin", "shared_inbox"]


class Contact(BaseModel):
    name: str
    title: str
    relevance: str
    linkedin: str | None = None
    email: EmailContact | None = None


class Route(BaseModel):
    """Where one letter goes; `address` is what the user copies into their mail client."""

    channel: Channel
    address: str
    confidence: AddressConfidence
    generic_inbox: bool = False


class Skipped(BaseModel):
    contact: str
    reason: str


class CompanyEvidence(BaseModel):
    hooks: list[Hook]
    verified_entities: list[str]


class Addressee(BaseModel):
    contact: Contact
    route: Route


class Recipients(BaseModel):
    addressees: list[Addressee]
    skipped: list[Skipped] = Field(default_factory=list)


class LetterBriefBuilder:
    def __init__(self, company: str, dossier: CompanyDossier) -> None:
        self._company = company
        self._dossier = dossier

    def recipients(self) -> Recipients:
        """One route per contact, best channel first: own email, LinkedIn, guess, shared inbox."""
        addressees: list[Addressee] = []
        skipped: list[Skipped] = []
        for contact in self.contacts():
            route = self._route(contact)
            if route is None:
                skipped.append(Skipped(contact=contact.name, reason="no email or LinkedIn found"))
            else:
                addressees.append(Addressee(contact=contact, route=route))
        return Recipients(addressees=addressees, skipped=skipped)

    def contacts(self) -> list[Contact]:
        """Key people first, then tech decision makers not already listed, up to five."""
        found: dict[str, Contact] = {}
        if self._dossier.contacts:
            for person in self._dossier.contacts.key_people:
                found.setdefault(
                    name_key(person.name), Contact.model_validate(person, from_attributes=True)
                )
        if self._dossier.tech:
            for maker in self._dossier.tech.tech_decision_makers:
                contact = Contact(
                    name=maker.name, title=maker.title, relevance=maker.likely_priorities
                )
                found.setdefault(name_key(maker.name), contact)
        return list(found.values())[:MAX_CONTACTS]

    def evidence(self, fields: dict[str, FieldValue]) -> CompanyEvidence:
        hooks = [*self._news_hooks(), *self._growth_hooks(), *self._tech_hooks()]
        hooks += [
            Hook(signal=value.quote, source="research", source_url=value.source_url)
            for value in fields.values()
            if value.quote
        ]
        entities = {self._company, *self._dossier_entities()}
        for value in fields.values():
            entities |= _values(value)
        return CompanyEvidence(hooks=hooks[:MAX_HOOKS], verified_entities=sorted(entities - {""}))

    def _route(self, contact: Contact) -> Route | None:
        email = contact.email
        if email and email.kind == "published":
            return Route(channel="email", address=email.address, confidence="published")
        if contact.linkedin:
            return Route(channel="dm", address=contact.linkedin, confidence="linkedin")
        if email:
            return Route(channel="email", address=email.address, confidence="guessed")
        shared = self._shared_inbox()
        if shared:
            return Route(
                channel="email", address=shared, confidence="shared_inbox", generic_inbox=True
            )
        return None

    def _shared_inbox(self) -> str | None:
        contacts = self._dossier.contacts
        published = (
            [e.address for e in contacts.emails if e.kind == "published"] if contacts else []
        )
        return published[0] if published else None

    def _news_hooks(self) -> list[Hook]:
        news = self._dossier.news
        ranked = sorted(
            news.items if news else [],
            key=lambda n: ({"high": 0, "medium": 1, "low": 2}[n.timing_relevance], n.date),
        )
        return [
            Hook(signal=f"{n.title} ({n.date}): {n.summary}", source="news", source_url=n.url)
            for n in ranked
        ]

    def _growth_hooks(self) -> list[Hook]:
        growth = self._dossier.growth
        if growth is None:
            return []
        hooks = [Hook(signal=growth.trajectory_evidence, source="growth")]
        postings = growth.job_postings
        if postings.total_open:
            roles = ", ".join(postings.notable_roles[:3])
            hooks.append(Hook(signal=f"{postings.total_open} open roles: {roles}", source="growth"))
        funding = growth.funding
        if funding and funding.last_round:
            when = f" ({funding.date})" if funding.date else ""
            hooks.append(Hook(signal=f"Last round: {funding.last_round}{when}", source="growth"))
        return hooks

    def _tech_hooks(self) -> list[Hook]:
        tech = self._dossier.tech
        if tech is None:
            return []
        notes = [tech.tech_blog, tech.open_source, tech.conference_presence]
        return [Hook(signal=note, source="tech") for note in notes if note]

    def _dossier_entities(self) -> set[str]:
        entities: set[str] = set()
        if self._dossier.news:
            for n in self._dossier.news.items:
                entities |= {n.title, n.date}
        if self._dossier.growth:
            growth = self._dossier.growth
            entities |= {str(growth.job_postings.total_open), *growth.job_postings.notable_roles}
            if growth.funding:
                funding = growth.funding
                entities |= {
                    funding.last_round or "",
                    funding.total_raised or "",
                    funding.date or "",
                }
                entities |= set(funding.key_investors)
        if self._dossier.tech:
            stack = self._dossier.tech.stack
            for names in stack.model_dump().values():
                entities |= set(names)
        return entities


def angles_from(strategy_angles: list[OutreachAngle]) -> list[Angle]:
    return [
        Angle(name=a.name, pain_point=a.pain_point, value_prop=a.value_prop)
        for a in strategy_angles
    ]


def _values(value: FieldValue) -> set[str]:
    raw = value.value
    if isinstance(raw, list):
        return set(raw)
    return {str(raw)} if raw is not None else set()
