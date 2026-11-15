"""Basic enrichment of listings and gigs: who posted them and who is named, checked in Python."""

import asyncio
import logging
import re
from urllib.parse import urlsplit

from pydantic import BaseModel

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.domain import IdentifierKind
from mozg_outreach.pipelines.leads.models import (
    IdentifiedCompany,
    LeadIdentity,
    LeadInput,
    LeadMode,
    NamedPerson,
)
from mozg_outreach.pipelines.seeker.models import Listing
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.registry.identifiers import normalize, normalize_domain

logger = logging.getLogger(__name__)

SUMMARY_CHARS = 1500


class IdentifyOutcome(BaseModel):
    identity: LeadIdentity | None
    concerns: list[str]


class LeadIdentifier:
    def __init__(self, steps: PipelineSteps) -> None:
        self._steps = steps

    async def identify(self, lead: LeadInput) -> IdentifyOutcome:
        try:
            found = await self._steps.run("shared", "lead-identifier", lead, LeadIdentity)
        except (AgentRunError, AgentOutputError) as exc:
            # The lead stays in the report, just without a linked company or people.
            logger.warning("lead identification for %r failed: %s", lead.title, exc)
            return IdentifyOutcome(identity=None, concerns=[f"{lead.title}: {exc}"])
        identity = accept_identity(found)
        return IdentifyOutcome(
            identity=identity, concerns=[f"{lead.title}: {c}" for c in identity.concerns]
        )

    async def identify_many(self, leads: list[LeadInput]) -> list[IdentifyOutcome]:
        return list(await asyncio.gather(*(self.identify(lead) for lead in leads)))


def lead_input(
    listing: Listing, mode: LeadMode, summary: str, hidden: frozenset[str] = frozenset()
) -> LeadInput:
    """`hidden` holds board names: a client shown as the board itself is no client name at all."""
    named = listing.company.strip()
    organization = None if not named or named.casefold() in hidden else named
    return LeadInput(
        mode=mode,
        title=listing.title,
        organization=organization,
        url=listing.url,
        apply_url=listing.apply_url,
        location=listing.location_string,
        summary=summary[:SUMMARY_CHARS],
    )


def accept_identity(identity: LeadIdentity) -> LeadIdentity:
    """Keep only facts the cited page shows: a domain on its own page, names and emails quoted."""
    concerns = list(identity.concerns)
    company = _accept_company(identity.company, concerns) if identity.company else None
    people = [p for p in (_accept_person(p, concerns) for p in identity.people) if p is not None]
    if len(concerns) > len(identity.concerns):
        logger.info("lead identity: %d facts dropped", len(concerns) - len(identity.concerns))
    return LeadIdentity(company=company, people=people, concerns=concerns)


def _accept_company(company: IdentifiedCompany, concerns: list[str]) -> IdentifiedCompany:
    domain, linkedin = company.domain, company.linkedin
    if domain is not None:
        host = normalize_domain(domain.value)
        page_host = normalize_domain(urlsplit(domain.source_url).hostname)
        on_page = host is not None and (
            (page_host is not None and (page_host == host or page_host.endswith(f".{host}")))
            or host in domain.quote.casefold()
        )
        if not on_page:
            concerns.append(f"Dropped domain {domain.value!r} for {company.name}: not on its page.")
        domain = domain.model_copy(update={"value": host}) if on_page else None
    if linkedin is not None and normalize(IdentifierKind.LINKEDIN, linkedin.value) is None:
        concerns.append(f"Dropped LinkedIn {linkedin.value!r} for {company.name}: not a page URL.")
        linkedin = None
    return company.model_copy(update={"domain": domain, "linkedin": linkedin})


def _accept_person(person: NamedPerson, concerns: list[str]) -> NamedPerson | None:
    quote = _plain(person.quote)
    if _plain(person.name) not in quote:
        concerns.append(f"Dropped {person.name}: the quote doesn't name them.")
        return None
    if person.email and _plain(person.email) not in quote:
        concerns.append(f"Dropped the email for {person.name}: it isn't in the quote.")
        return person.model_copy(update={"email": None})
    return person


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()
