"""Biz per-company enrichment: five independent agents run concurrently over one company."""

import asyncio
import logging

from pydantic import BaseModel

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.domain import ItemCandidate
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

logger = logging.getLogger(__name__)


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
        return CompanyDossier(
            reputation=_section(company, "reputation", reputation, concerns),
            contacts=_section(company, "contacts", contacts, concerns),
            news=_section(company, "news", news, concerns),
            growth=_section(company, "growth", growth, concerns),
            tech=_section(company, "tech", tech, concerns),
            concerns=concerns,
        )

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


def brief_from_candidate(candidate: ItemCandidate, strategy: Strategy) -> CompanyBrief:
    """Combine a discovered company with the approved strategy's targeting context."""

    def text(field: str) -> str | None:
        value = candidate.fields[field].value if field in candidate.fields else None
        return value if isinstance(value, str) else None

    return CompanyBrief(
        name=candidate.title,
        domain=text("domain"),
        location=text("location"),
        sector=text("sector"),
        description=text("description"),
        target_profile=strategy.target_profile,
        outreach_angles=strategy.outreach_angles,
    )
