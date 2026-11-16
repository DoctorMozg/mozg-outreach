"""Directories Python reads itself: structured pages and registers, no agent and no AI cost."""

import io
import json
import logging
import re
from abc import ABC, abstractmethod
from html.parser import HTMLParser
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pypdf import PdfReader

from mozg_outreach.pipelines.biz.models import DirectorySource, ScoutedCompany
from mozg_outreach.pipelines.biz.regions import UAE_PLACES, mentions
from mozg_outreach.sources.base import RateLimiter

logger = logging.getLogger(__name__)

PAGE_INTERVAL_S = 1.0


class DirectoryError(RuntimeError):
    """A known directory changed shape or could not be fetched."""


class KnownDirectory(ABC):
    name: str
    url: str
    sector_words: tuple[str, ...]
    place_words: tuple[str, ...] = UAE_PLACES
    notes: str

    def serves(self, sector: str | None, region: str | None) -> bool:
        if not sector or not region:
            return False
        return mentions(sector, self.sector_words) and mentions(region, self.place_words)

    def handles(self, url: str) -> bool:
        return url.rstrip("/") == self.url.rstrip("/")

    def source(self, sector: str | None) -> DirectorySource:
        return DirectorySource(
            name=self.name,
            url=self.url,
            type="government_registry",
            estimated_count=None,
            relevance=9,
            access_notes=f"Read by the pipeline, not an agent. {self.notes}",
            search_queries_used=[],
            sector=sector,
        )

    @abstractmethod
    async def read(
        self, client: httpx.AsyncClient, url: str, limit: int
    ) -> list[ScoutedCompany]: ...


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    try:
        response = await client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise DirectoryError(f"fetching {url} failed: {exc}") from exc
    return response


class _Broker(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    slug: str = Field(alias="urlSlug")
    name: str
    location: str | None = None
    agents: int = Field(default=0, alias="totalAgents")
    listings: int = Field(default=0, alias="totalProperties")
    licence: str | None = Field(default=None, alias="licenseNumber")


class _BrokerPage(BaseModel):
    data: list[_Broker]


class PropertyFinderBrokers(KnownDirectory):
    name = "Property Finder broker directory"
    url = "https://www.propertyfinder.ae/en/find-broker/search"
    sector_words = ("real estate", "property", "properties", "realty")
    notes = "UAE brokerages ranked by Property Finder, with agent and listing counts."
    max_pages = 5
    # Branches and holiday-home lets with a handful of agents are below the SME size band.
    min_agents = 10

    def __init__(self) -> None:
        self._pace = RateLimiter(PAGE_INTERVAL_S)

    async def read(self, client: httpx.AsyncClient, url: str, limit: int) -> list[ScoutedCompany]:
        found: list[ScoutedCompany] = []
        for page in range(1, self.max_pages + 1):
            await self._pace.wait()
            try:
                response = await _get(client, url if page == 1 else f"{url}?page={page}")
                brokers = self._brokers(response.text, url)
            except DirectoryError:
                if page == 1:
                    raise
                logger.warning("Property Finder page %d failed; keeping %d", page, len(found))
                break
            found += [self._company(b) for b in brokers if b.agents >= self.min_agents]
            if len(found) >= limit or not brokers:
                break
        return found[:limit]

    @staticmethod
    def _brokers(page: str, url: str) -> list[_Broker]:
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
        if not match:
            raise DirectoryError(f"{url} has no __NEXT_DATA__ block")
        try:
            raw = json.loads(match[1])["props"]["pageProps"]["brokers"]
            return _BrokerPage.model_validate(raw).data
        except (KeyError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            raise DirectoryError(f"{url} broker data moved: {exc!r}") from exc

    @staticmethod
    def _company(broker: _Broker) -> ScoutedCompany:
        name = broker.name.strip()
        where = f"{broker.location}, United Arab Emirates" if broker.location else None
        return ScoutedCompany(
            name=name,
            domain=None,
            sector="Real estate brokerage",
            location=where or "United Arab Emirates",
            founded=None,
            description=(
                f"{broker.agents} agents and {broker.listings} listings on "
                f"Property Finder; licence {broker.licence or 'not shown'}."
            ),
            source_url=f"https://www.propertyfinder.ae/en/broker/{broker.slug}-{broker.id}",
            evidence_quote=name,
            evidence="page",
        )


class _TableRows(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            self._cell = []
        elif self._cell is not None:
            self._cell.append("\n")  # an element inside a cell, e.g. one licensed activity

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self.rows:
            parts = (" ".join(line.split()) for line in "".join(self._cell).splitlines())
            self.rows[-1].append("; ".join(part for part in parts if part))
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


class VaraRegister(KnownDirectory):
    name = "VARA public register (Dubai virtual asset service providers)"
    url = "https://www.vara.ae/en/licenses-and-register/public-register/"
    sector_words = ("fintech", "fintechs", "virtual asset", "crypto", "digital asset", "vasp")
    notes = "Dubai VARA licence holders with licensed activities and issue dates."

    async def read(self, client: httpx.AsyncClient, url: str, limit: int) -> list[ScoutedCompany]:
        parser = _TableRows()
        parser.feed((await _get(client, url)).text)
        rows = [r for r in parser.rows if len(r) >= 3 and r[0] and r[0] != "Licensee"]
        if not rows:
            raise DirectoryError(f"{url} has no register table")
        active = [r for r in rows if any(cell == "Active" for cell in r)]
        return [self._company(row, url) for row in active[:limit]]

    @staticmethod
    def _company(row: list[str], url: str) -> ScoutedCompany:
        name, licence = row[0], row[1]
        return ScoutedCompany(
            name=name,
            domain=None,
            sector="Virtual asset service provider",
            location="Dubai, United Arab Emirates",
            founded=None,
            description=f"VARA {licence}: {'; '.join(row[3:-3]) or 'activities not shown'}.",
            source_url=url,
            evidence_quote=name,
            evidence="page",
        )


_CBUAE_ROW = re.compile(
    r"(?:^|\s)\d{1,3}\.?\s+([A-Za-z][A-Za-z0-9&'().,/\- ]*?)\s+Insurance\s+(Company|Broker)\s+"
    r"(National|Foreign|takaful|Takaful)\s+(\d{2,3})\b"
)


class CbuaeInsuranceRegister(KnownDirectory):
    name = "CBUAE register of insurers and insurance brokers"
    # Republished monthly under a new random path; the source researcher may find a newer one.
    url = "https://centralbank.ae/media/4azoit5n/cb-register-august-of-2026.pdf"
    sector_words = ("insurance", "insurer", "insurers", "takaful")
    notes = "Monthly Central Bank PDF of licensed insurers and brokers, with licence numbers."

    def handles(self, url: str) -> bool:
        return bool(
            re.match(r"https?://(www\.)?centralbank\.ae/media/.+register.*\.pdf$", url, re.I)
        )

    async def read(self, client: httpx.AsyncClient, url: str, limit: int) -> list[ScoutedCompany]:
        pdf = (await _get(client, url)).content
        try:
            text = " ".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)
        except Exception as exc:  # pypdf raises a wide range of types on a damaged file
            raise DirectoryError(f"{url} is not a readable PDF: {exc!r}") from exc
        rows = self.rows(text, url)
        if not rows:
            raise DirectoryError(f"{url} has no insurer or broker rows")
        return rows[:limit]

    @classmethod
    def rows(cls, text: str, url: str) -> list[ScoutedCompany]:
        """Insurers and brokers from the PDF text; adjusters and actuaries are skipped."""
        rows = [cls._company(m, url) for m in _CBUAE_ROW.finditer(" ".join(text.split()))]
        # Brokers are SME-sized far more often than insurers, so they lead.
        rows.sort(key=lambda c: c.sector != "Insurance broker")
        return rows

    @staticmethod
    def _company(match: re.Match[str], url: str) -> ScoutedCompany:
        name, kind, category, number = match.groups()
        sector: Literal["Insurance broker", "Insurance company"] = (
            "Insurance broker" if kind == "Broker" else "Insurance company"
        )
        return ScoutedCompany(
            name=name.strip(" .,"),
            domain=None,
            sector=sector,
            location="United Arab Emirates",
            founded=None,
            description=(
                f"CBUAE-licensed {sector.lower()} ({category.lower()}), register no. {number}."
            ),
            source_url=url,
            evidence_quote=match[0].strip(),
            evidence="page",
        )


class Directories:
    """The known directories plus the HTTP client that reads them."""

    def __init__(self, client: httpx.AsyncClient, catalog: tuple[KnownDirectory, ...]) -> None:
        self._client = client
        self._catalog = catalog

    def serving(self, sector: str | None, region: str | None) -> list[DirectorySource]:
        return [d.source(sector) for d in self._catalog if d.serves(sector, region)]

    def reads(self, source: DirectorySource) -> bool:
        return self._reader(source.url) is not None

    async def read(self, source: DirectorySource, limit: int) -> list[ScoutedCompany]:
        reader = self._reader(source.url)
        if reader is None:
            raise DirectoryError(f"no reader for {source.url}")
        companies = await reader.read(self._client, source.url, limit)
        logger.info("read %d companies from %s", len(companies), source.name)
        return companies

    def _reader(self, url: str) -> KnownDirectory | None:
        return next((d for d in self._catalog if d.handles(url)), None)


def default_catalog() -> tuple[KnownDirectory, ...]:
    return (PropertyFinderBrokers(), VaraRegister(), CbuaeInsuranceRegister())
