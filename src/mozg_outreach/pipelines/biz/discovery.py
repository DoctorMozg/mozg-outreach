"""Biz discovery phases. The run manager owns the strategy approval gate, so each phase is a
separate call: review_brief, propose_fields, [research_climate], plan_strategy, [gate],
find_sources, scout."""

import asyncio
import logging
import math
import re

from pydantic import BaseModel

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.domain import FieldSpec, FieldValue, ItemCandidate, ItemKind
from mozg_outreach.pipelines.biz.models import (
    BizBrief,
    BriefReview,
    ClimateReport,
    ClimateResearchInput,
    ClimateSourceInput,
    ClimateSources,
    DirectorySource,
    FieldProposal,
    FieldProposalInput,
    ScoutedCompany,
    ScoutInput,
    ScoutResult,
    SourceList,
    SourceResearchInput,
    Strategy,
    StrategyInput,
)
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.registry.identifiers import normalize_domain

logger = logging.getLogger(__name__)

MIN_SOURCE_RELEVANCE = 5
MAX_SOURCES = 10
MIN_SCOUT_COUNT = 10
CLIMATE_RECENCY_MONTHS = 12


class DiscoveryError(RuntimeError):
    """Discovery can't continue: no usable sources, or every scout failed."""


class ScoutOutcome(BaseModel):
    candidates: list[ItemCandidate]
    concerns: list[str]


class BizDiscovery:
    def __init__(self, steps: PipelineSteps) -> None:
        self._steps = steps

    async def review_brief(self, brief: BizBrief) -> BriefReview:
        return await self._steps.run("shared", "brief-reviewer", brief, BriefReview)

    async def propose_fields(self, brief: BizBrief, current: FieldSpec | None) -> FieldProposal:
        payload = FieldProposalInput(mode="biz", brief=brief, current_spec=current)
        return await self._steps.run("shared", "field-proposer", payload, FieldProposal)

    async def plan_strategy(self, request: StrategyInput) -> Strategy:
        return await self._steps.run("biz", "outreach-strategist", request, Strategy)

    async def research_climate(self, brief: BizBrief, region: str) -> ClimateReport:
        industry = ", ".join(brief.icp.industries)
        found = await self._steps.run(
            "biz",
            "outreach-climate-source-finder",
            ClimateSourceInput(industry=industry, region=region),
            ClimateSources,
        )
        if not found.sources:
            raise DiscoveryError(f"no climate sources found for {industry} in {region}")
        payload = ClimateResearchInput(
            industry=industry,
            region=region,
            sources=found.sources,
            recency_months=CLIMATE_RECENCY_MONTHS,
        )
        report = await self._steps.run("biz", "outreach-climate-researcher", payload, ClimateReport)
        report.concerns = [*found.concerns, *report.concerns]
        return report

    async def find_sources(self, strategy: Strategy, region: str | None) -> SourceList:
        payload = SourceResearchInput(
            target_description=describe_target(strategy, region),
            source_hints=strategy.source_hints,
        )
        found = await self._steps.run("biz", "outreach-source-researcher", payload, SourceList)
        usable = sorted(
            (s for s in found.sources if s.relevance >= MIN_SOURCE_RELEVANCE),
            key=lambda s: s.relevance,
            reverse=True,
        )[:MAX_SOURCES]
        if not usable:
            raise DiscoveryError(f"no source reached relevance {MIN_SOURCE_RELEVANCE}")
        logger.info("kept %d of %d sources", len(usable), len(found.sources))
        return SourceList(sources=usable, concerns=found.concerns)

    async def scout(
        self,
        strategy: Strategy,
        sources: list[DirectorySource],
        *,
        region: str | None,
        limit: int,
    ) -> ScoutOutcome:
        if not sources:
            raise DiscoveryError("scout needs at least one source")
        per_source = max(MIN_SCOUT_COUNT, math.ceil(limit / len(sources)))
        payloads = [
            ScoutInput(
                source=s,
                target_profile=strategy.target_profile,
                sector=strategy.target_profile.sectors[0]
                if strategy.target_profile.sectors
                else None,
                region=region,
                count_limit=per_source,
            )
            for s in sources
        ]
        results = await asyncio.gather(
            *(self._steps.run("biz", "outreach-scout", p, ScoutResult) for p in payloads),
            return_exceptions=True,
        )

        companies: list[ScoutedCompany] = []
        concerns: list[str] = []
        failed = 0
        for source, result in zip(sources, results, strict=True):
            if isinstance(result, AgentRunError | AgentOutputError):
                # One broken source shouldn't sink the run; the others still count.
                logger.warning("scout for %s failed: %s", source.name, result)
                concerns.append(f"Scout for {source.name} failed: {result}")
                failed += 1
            elif isinstance(result, BaseException):
                raise result
            else:
                companies.extend(result.companies)
                concerns.extend(f"{source.name}: {c}" for c in result.concerns)
        if failed == len(sources):
            raise DiscoveryError(f"all {failed} scouts failed: {concerns}")
        return ScoutOutcome(candidates=dedup_companies(companies), concerns=concerns)


def describe_target(strategy: Strategy, region: str | None) -> str:
    profile = strategy.target_profile
    geography = [region] if region else profile.geography
    parts = [
        f"{', '.join(profile.sectors) or 'companies'} in {', '.join(geography) or 'any region'}",
        f"stage: {', '.join(profile.company_stage)}" if profile.company_stage else "",
        f"size: {profile.size_range}" if profile.size_range else "",
    ]
    return "; ".join(p for p in parts if p)


def dedup_companies(companies: list[ScoutedCompany]) -> list[ItemCandidate]:
    """Merge scout rows by domain (or name when no domain); the first row's fields win."""
    merged: dict[str, ItemCandidate] = {}
    for company in companies:
        name = company.name.strip()
        if not name or name.startswith("_"):
            continue
        domain = normalize_domain(company.domain)
        key = domain or f"name:{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}"
        if key in merged:
            sources = merged[key].fields["source_urls"]
            if isinstance(sources.value, list) and company.source_url not in sources.value:
                sources.value.append(company.source_url)
            if company.evidence == "page" and merged[key].fields["evidence"].value != "page":
                merged[key].fields["evidence"] = _evidence_field(company)
            continue
        merged[key] = ItemCandidate(
            kind=ItemKind.COMPANY,
            dedup_key=key,
            title=name,
            fields=_company_fields(company, domain),
        )
    return list(merged.values())


def _company_fields(company: ScoutedCompany, domain: str | None) -> dict[str, FieldValue]:
    def evidence(value: str | None) -> FieldValue:
        return FieldValue(value=value, source_url=company.source_url, quote=company.evidence_quote)

    fields = {
        "domain": evidence(domain),
        "sector": evidence(company.sector),
        "location": evidence(company.location),
        "founded": evidence(company.founded),
        "description": evidence(company.description),
    }
    fields = {k: v for k, v in fields.items() if v.value is not None}
    fields["source_urls"] = FieldValue(value=[company.source_url])
    fields["evidence"] = _evidence_field(company)
    return fields


def _evidence_field(company: ScoutedCompany) -> FieldValue:
    return FieldValue(
        value=company.evidence, source_url=company.source_url, quote=company.evidence_quote
    )
