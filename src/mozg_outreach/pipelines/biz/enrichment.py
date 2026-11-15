"""Biz per-company enrichment: five independent agents run concurrently over one company."""

import asyncio
import logging

from pydantic import BaseModel

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.domain import FieldValue, ItemCandidate
from mozg_outreach.pipelines.biz.enrichment_models import (
    CompanyBrief,
    CompanyDossier,
    ContactsResult,
    GrowthResult,
    NewsResult,
    ReputationResult,
    TechResult,
)
from mozg_outreach.pipelines.biz.models import Strategy
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.registry.identifiers import normalize_domain

logger = logging.getLogger(__name__)


# Third-party sites that describe a company but are never its own website.
LISTING_DOMAINS = frozenset(
    {"trustpilot.com", "chambers.com", "legal500.com", "linkedin.com", "glassdoor.com"}
    | {"indeed.com", "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com"}
    | {"crunchbase.com", "mondaq.com", "google.com", "yelp.com", "clutch.co", "zoominfo.com"}
    | {"bloomberg.com", "wikipedia.org", "g2.com", "capterra.com", "pitchbook.com"}
    | {"dnb.com", "yellowpages.com", "companieshouse.gov.uk", "opencorporates.com"}
)


class CompanyDomain:
    """Decides which host may be recorded as a company's own domain."""

    @staticmethod
    def usable(raw: str | None) -> str | None:
        host = normalize_domain(raw)
        if host is None:
            return None
        listed = any(host == d or host.endswith(f".{d}") for d in LISTING_DOMAINS)
        return None if listed else host

    @classmethod
    def from_dossier(cls, dossier: CompanyDossier) -> FieldValue | None:
        """The website the contact finder cited, as an envelope; None without a usable source."""
        site = dossier.contacts.website if dossier.contacts else None
        host = cls.usable(site.url) if site else None
        if site is None or host is None or not site.source_url.strip():
            return None
        return FieldValue(value=host, source_url=site.source_url, quote=site.quote or None)


class BizEnrichment:
    def __init__(self, steps: PipelineSteps) -> None:
        self._steps = steps

    async def enrich(self, company: CompanyBrief) -> CompanyDossier:
        reputation, contacts, news, growth, tech = await asyncio.gather(
            self._steps.run("biz", "outreach-scanner", company, ReputationResult),
            self._steps.run("biz", "outreach-contact-finder", company, ContactsResult),
            self._steps.run("biz", "outreach-news-finder", company, NewsResult),
            self._steps.run("biz", "outreach-growth-analyst", company, GrowthResult),
            self._steps.run("biz", "outreach-tech-analyst", company, TechResult),
            return_exceptions=True,
        )
        concerns: list[str] = []
        dossier = CompanyDossier(
            reputation=_section(company, "reputation", reputation, concerns),
            contacts=_section(company, "contacts", contacts, concerns),
            news=_section(company, "news", news, concerns),
            growth=_section(company, "growth", growth, concerns),
            tech=_section(company, "tech", tech, concerns),
            concerns=concerns,
        )
        _drop_unevidenced_people(dossier)
        return dossier

    async def enrich_many(self, companies: list[CompanyBrief]) -> list[CompanyDossier]:
        # The runner's semaphore caps real concurrency, so fan out without another limit.
        return list(await asyncio.gather(*(self.enrich(c) for c in companies)))


def _section[TResult: BaseModel](
    company: CompanyBrief, label: str, result: TResult | BaseException, concerns: list[str]
) -> TResult | None:
    if isinstance(result, AgentRunError | AgentOutputError):
        # One broken agent shouldn't sink the dossier; the other sections still count.
        logger.warning("%s for %s failed: %s", label, company.name, result)
        concerns.append(f"{label.capitalize()} for {company.name} failed: {result}")
        return None
    if isinstance(result, BaseException):
        raise result
    return result


def _drop_unevidenced_people(dossier: CompanyDossier) -> None:
    """People without a page quote are unverifiable; drop them before scoring and the registry."""
    if dossier.contacts:
        kept = [p for p in dossier.contacts.key_people if p.quote.strip()]
        dossier.concerns += [
            f"Dropped {p.name}: no quote from a page"
            for p in dossier.contacts.key_people
            if not p.quote.strip()
        ]
        dossier.contacts.key_people = kept
    if dossier.tech:
        makers = [m for m in dossier.tech.tech_decision_makers if m.quote.strip()]
        dossier.concerns += [
            f"Dropped {m.name}: no quote from a page"
            for m in dossier.tech.tech_decision_makers
            if not m.quote.strip()
        ]
        dossier.tech.tech_decision_makers = makers


def brief_from_candidate(candidate: ItemCandidate, strategy: Strategy) -> CompanyBrief:
    """Combine a discovered company with the approved strategy's targeting context."""

    def text(field: str) -> str | None:
        value = candidate.fields[field].value if field in candidate.fields else None
        return value if isinstance(value, str) else None

    urls = candidate.fields.get("source_urls")
    pages = urls.value if urls and isinstance(urls.value, list) else []
    return CompanyBrief(
        name=candidate.title,
        domain=CompanyDomain.usable(text("domain")),
        location=text("location"),
        sector=text("sector"),
        description=text("description"),
        decision_maker=text("decision_maker"),
        preferred_channel=text("preferred_channel"),
        recent_trigger=text("recent_trigger"),
        source_urls=[u for u in pages if isinstance(u, str)],
        target_profile=strategy.target_profile,
        outreach_angles=strategy.outreach_angles,
    )
