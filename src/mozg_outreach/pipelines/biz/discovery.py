"""Biz discovery phases. The run manager owns the strategy approval gate, so each phase is a
separate call: review_brief, [research_climate], plan_strategy, [gate],
find_sources, scout. Sources and scouts fan out per target sector."""

import asyncio
import logging
import math
import re

from pydantic import BaseModel, Field

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.domain import FieldValue, ItemCandidate, ItemKind
from mozg_outreach.pipelines.biz.directories import Directories, DirectoryError
from mozg_outreach.pipelines.biz.models import (
    BizBrief,
    BriefReview,
    ClimateReport,
    ClimateResearchInput,
    ClimateSourceInput,
    ClimateSources,
    DirectorySource,
    ScoutedCompany,
    ScoutInput,
    ScoutResult,
    SourceList,
    SourceResearchInput,
    Strategy,
    StrategyInput,
)
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.registry.identifiers import name_key, normalize_domain

logger = logging.getLogger(__name__)

MIN_SOURCE_RELEVANCE = 5
MIN_SCOUT_COUNT = 5
# Scouts ask for more than the quota because dedup and the fit check drop some.
OVERFETCH = 1.5
CLIMATE_RECENCY_MONTHS = 12


class DiscoveryError(RuntimeError):
    """Discovery can't continue: no usable sources, or every scout failed."""


class ScoutOutcome(BaseModel):
    candidates: list[ItemCandidate]
    concerns: list[str]
    mix: dict[str, int] = Field(default_factory=dict, description="Picked companies per sector.")


class BizDiscovery:
    def __init__(self, steps: PipelineSteps, directories: Directories | None = None) -> None:
        self._steps = steps
        self._directories = directories

    async def review_brief(self, brief: BizBrief) -> BriefReview:
        return await self._steps.run("shared", "brief-reviewer", brief, BriefReview)

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

    async def find_sources(
        self, strategy: Strategy, region: str | None, per_sector: int
    ) -> SourceList:
        """Sources for every target sector, so an easy-to-read sector can't crowd out the rest."""
        sectors = _sectors(strategy)
        where = region or "; ".join(strategy.target_profile.geography) or None
        results = await asyncio.gather(
            *(self._research(strategy, region, s, per_sector) for s in sectors),
            return_exceptions=True,
        )
        sources: list[DirectorySource] = []
        concerns: list[str] = []
        for sector, result in zip(sectors, results, strict=True):
            label = sector_label(sector)
            known = self._directories.serving(sector, where) if self._directories else []
            found: list[DirectorySource] = []
            if isinstance(result, AgentRunError | AgentOutputError):
                logger.warning("source research for %s failed: %s", label, result)
                concerns.append(f"Source research for {label} failed: {result}")
            elif isinstance(result, BaseException):
                raise result
            else:
                known_urls = {k.url for k in known}
                found = [s for s in result.sources if s.url not in known_urls]
                concerns += [f"{label}: {c}" for c in result.concerns]
            picked = [*known, *(s.model_copy(update={"sector": sector}) for s in found)]
            if not picked:
                concerns.append(f"No readable directory for {label}; its scouts search the web.")
            sources += picked
        logger.info("kept %d sources for %d sectors", len(sources), len(sectors))
        return SourceList(sources=sources, concerns=concerns)

    async def _research(
        self, strategy: Strategy, region: str | None, sector: str | None, per_sector: int
    ) -> SourceList:
        payload = SourceResearchInput(
            sector=sector,
            target_description=describe_target(strategy, region, sector),
            source_hints=strategy.source_hints,
            max_sources=per_sector + 1,
        )
        found = await self._steps.run("biz", "outreach-source-researcher", payload, SourceList)
        usable = sorted(
            (s for s in found.sources if s.relevance >= MIN_SOURCE_RELEVANCE),
            key=lambda s: s.relevance,
            reverse=True,
        )
        return SourceList(sources=usable[:per_sector], concerns=found.concerns)

    async def scout(
        self,
        strategy: Strategy,
        sources: list[DirectorySource],
        *,
        region: str | None,
        limit: int,
    ) -> ScoutOutcome:
        """Scouts every sector to a quota, then merges sectors round-robin up to the limit."""
        sectors = _sectors(strategy)
        quota = math.ceil(limit / len(sectors))
        pool = CompanyPool()
        concerns: list[str] = []
        tried: dict[str | None, list[str]] = {s: [] for s in sectors}

        first = [
            task
            for sector in sectors
            for task in self._first_round(strategy, sources, sector, region, quota)
        ]
        found = await self._run_round(first, pool, concerns, tried)
        if not any(found):
            raise DiscoveryError(f"all {len(first)} scouts failed: {concerns}")

        short = [
            sector
            for sector in sectors
            if pool.count(sector) < quota and not _saturated(first, found, sector)
        ]
        retry = [
            _ScoutTask(
                sector=sector,
                payload=self._payload(
                    strategy,
                    None,
                    sector,
                    region,
                    count=max(MIN_SCOUT_COUNT, math.ceil((quota - pool.count(sector)) * OVERFETCH)),
                    exclude=pool.names(),
                    tried=tried[sector],
                ),
            )
            for sector in short
        ]
        if retry:
            logger.info("second scout round for %d short sectors", len(retry))
            await self._run_round(retry, pool, concerns, tried)

        picked = pool.balanced(sectors, limit)
        mix = {sector_label(s): sum(1 for c in picked if c.sector == s) for s in sectors}
        logger.info("sector mix: %s", mix)
        return ScoutOutcome(candidates=[c.candidate for c in picked], concerns=concerns, mix=mix)

    def _first_round(
        self,
        strategy: Strategy,
        sources: list[DirectorySource],
        sector: str | None,
        region: str | None,
        quota: int,
    ) -> list["_ScoutTask"]:
        own = [s for s in sources if s.sector == sector]
        if not own:
            payload = self._payload(strategy, None, sector, region, count=quota)
            return [_ScoutTask(sector=sector, payload=payload)]
        count = max(MIN_SCOUT_COUNT, math.ceil(quota * OVERFETCH / len(own)))
        return [
            _ScoutTask(sector=sector, payload=self._payload(strategy, s, sector, region, count))
            for s in own
        ]

    @staticmethod
    def _payload(
        strategy: Strategy,
        source: DirectorySource | None,
        sector: str | None,
        region: str | None,
        count: int,
        exclude: list[str] | None = None,
        tried: list[str] | None = None,
    ) -> ScoutInput:
        return ScoutInput(
            source=source,
            target_profile=strategy.target_profile,
            sector=sector,
            region=region,
            count_limit=count,
            exclude_names=exclude or [],
            queries_tried=tried or [],
        )

    async def _run_round(
        self,
        tasks: list["_ScoutTask"],
        pool: "CompanyPool",
        concerns: list[str],
        tried: dict[str | None, list[str]],
    ) -> list[int | None]:
        """New companies per task, or None for a failed one."""
        results = await asyncio.gather(*(self._scout_one(t) for t in tasks), return_exceptions=True)
        added: list[int | None] = []
        for task, result in zip(tasks, results, strict=True):
            name = task.payload.source.name if task.payload.source else "web search"
            where = f"{name} ({sector_label(task.sector)})"
            if isinstance(result, AgentRunError | AgentOutputError | DirectoryError):
                # One broken source shouldn't sink the run; the others still count.
                logger.warning("scout for %s failed: %s", where, result)
                concerns.append(f"Scout for {where} failed: {result}")
                added.append(None)
            elif isinstance(result, BaseException):
                raise result
            else:
                added.append(sum(pool.add(c, task.sector) for c in result.companies))
                tried[task.sector] += result.queries_used
                concerns.extend(f"{where}: {c}" for c in result.concerns)
        return added

    async def _scout_one(self, task: "_ScoutTask") -> ScoutResult:
        source = task.payload.source
        if source and self._directories and self._directories.reads(source):
            companies = await self._directories.read(source, task.payload.count_limit)
            return ScoutResult(companies=companies, concerns=[])
        return await self._steps.run("biz", "outreach-scout", task.payload, ScoutResult)


class _ScoutTask(BaseModel):
    sector: str | None
    payload: ScoutInput


def _saturated(tasks: list[_ScoutTask], found: list[int | None], sector: str | None) -> bool:
    """A web-search scout that found nothing new means a second one won't either."""
    own = [n for t, n in zip(tasks, found, strict=True) if t.sector == sector]
    searched = all(t.payload.source is None for t in tasks if t.sector == sector)
    return searched and all(not n for n in own)


def _sectors(strategy: Strategy) -> list[str | None]:
    return list(strategy.target_profile.sectors) or [None]


def sector_label(sector: str | None) -> str:
    """Short name for concerns: "Law firms (DIFC/ADGM...)" becomes "Law firms"."""
    if not sector:
        return "any sector"
    return re.split(r"[:(]", sector, maxsplit=1)[0].strip() or sector


def describe_target(strategy: Strategy, region: str | None, sector: str | None = None) -> str:
    profile = strategy.target_profile
    geography = [region] if region else profile.geography
    what = sector or ", ".join(profile.sectors) or "companies"
    parts = [
        f"{what} in {', '.join(geography) or 'any region'}",
        f"stage: {', '.join(profile.company_stage)}" if profile.company_stage else "",
        f"size: {profile.size_range}" if profile.size_range else "",
    ]
    return "; ".join(p for p in parts if p)


# Words that follow a firm's name without changing which firm it is: "Hadef & Partners" is Hadef.
_FIRM_WORDS = frozenset(
    {"llp", "fze", "fzco", "fzc", "fz", "pjsc", "psc", "dmcc", "wll", "opc", "company", "and"}
    | {"partners", "advocates", "legal", "consultants", "consultancy", "law", "firm", "attorneys"}
)


def company_key(name: str) -> str:
    """Name form that merges "Afridi & Angell LLP" with "Afridi & Angell Legal Consultants"."""
    words = name_key(name).split()
    while len(words) > 1:
        if words[-2:] == ["middle", "east"]:
            del words[-2:]
        elif words[-1] in _FIRM_WORDS:
            words.pop()
        else:
            break
    return "-".join(words)


class _Pooled(BaseModel):
    sector: str | None
    candidate: ItemCandidate


class CompanyPool:
    """Scouted companies merged by domain or firm name, remembering the sector that found each."""

    def __init__(self) -> None:
        self._entries: dict[str, _Pooled] = {}
        self._aliases: dict[str, str] = {}
        self._order: list[str] = []

    def add(self, company: ScoutedCompany, sector: str | None) -> bool:
        """True when the company is new to the pool."""
        name = company.name.strip()
        if not name or name.startswith("_"):
            return False
        domain = normalize_domain(company.domain)
        keys = [k for k in (domain, f"name:{company_key(name)}") if k and k != "name:"]
        if not keys:
            return False
        known = next((self._aliases[k] for k in keys if k in self._aliases), None)
        if known:
            self._merge(self._entries[known].candidate, company, domain)
            self._aliases.update({k: known for k in keys})
            return False
        primary = keys[0]
        self._entries[primary] = _Pooled(
            sector=sector,
            candidate=ItemCandidate(
                kind=ItemKind.COMPANY,
                dedup_key=primary,
                title=name,
                fields=_company_fields(company, domain),
            ),
        )
        self._aliases.update({k: primary for k in keys})
        self._order.append(primary)
        return True

    def count(self, sector: str | None) -> int:
        return sum(1 for e in self._entries.values() if e.sector == sector)

    def names(self) -> list[str]:
        return [self._entries[k].candidate.title for k in self._order]

    def candidates(self) -> list[ItemCandidate]:
        return [self._entries[k].candidate for k in self._order]

    def balanced(self, sectors: list[str | None], limit: int) -> list[_Pooled]:
        """Round-robin over sectors in strategy order; a short sector's slots go to the others."""
        queues = {
            s: [self._entries[k] for k in self._order if self._entries[k].sector == s]
            for s in sectors
        }
        picked: list[_Pooled] = []
        while len(picked) < limit and any(queues.values()):
            for sector in sectors:
                if queues[sector] and len(picked) < limit:
                    picked.append(queues[sector].pop(0))
        return picked

    @staticmethod
    def _merge(candidate: ItemCandidate, company: ScoutedCompany, domain: str | None) -> None:
        sources = candidate.fields["source_urls"]
        if isinstance(sources.value, list) and company.source_url not in sources.value:
            sources.value.append(company.source_url)
        if company.evidence == "page" and candidate.fields["evidence"].value != "page":
            candidate.fields["evidence"] = _evidence_field(company)
        if domain and "domain" not in candidate.fields:
            candidate.fields["domain"] = FieldValue(
                value=domain, source_url=company.source_url, quote=company.evidence_quote
            )


def dedup_companies(companies: list[ScoutedCompany]) -> list[ItemCandidate]:
    """Merge scout rows by domain or firm name; the first row's fields win."""
    pool = CompanyPool()
    for company in companies:
        pool.add(company, None)
    return pool.candidates()


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
