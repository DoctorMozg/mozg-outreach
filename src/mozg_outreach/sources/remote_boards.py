"""Remote job boards with open feeds: Himalayas, We Work Remotely and Working Nomads."""

import logging
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx
from pydantic import Field, field_validator

from mozg_outreach.sources.base import AdapterConfig, HttpFeedAdapter, RawRecord
from mozg_outreach.sources.models import SourceItem, SourceQuery
from mozg_outreach.sources.text import html_to_text

logger = logging.getLogger(__name__)

REMOTE = "remote"


class HimalayasConfig(AdapterConfig):
    max_pages: int = Field(default=2, ge=1, le=10, description="Search pages per term, 20 each.")
    max_terms: int = Field(default=6, ge=1, le=20)


class HimalayasJob(RawRecord):
    title: str
    companyName: str | None = None
    description: str = ""
    applicationLink: str
    guid: str
    pubDate: datetime | None = None
    locationRestrictions: list[str] = Field(default_factory=list)
    employmentType: str | None = None
    categories: list[str] = Field(default_factory=list)


class HimalayasAdapter(HttpFeedAdapter):
    name = "himalayas"
    license_note = (
        "Himalayas API terms: show a visible link to the job on Himalayas, use applicationLink "
        "unchanged for applying, never pass the jobs on to other job boards or aggregators."
    )
    SEARCH = "https://himalayas.app/jobs/api/search"

    def __init__(
        self, client: httpx.AsyncClient, config: HimalayasConfig, *, min_interval: float = 1.0
    ) -> None:
        super().__init__(client, min_interval)
        self._config = config

    async def _collect(self, query: SourceQuery, errors: list[str]) -> list[SourceItem]:
        """Searches each skill; the browse feed is all 100k jobs and useless unfiltered."""
        if not query.search_terms:
            errors.append(f"{self.name}: no search terms, so nothing was searched")
            return []
        found: dict[str, SourceItem] = {}
        fetched_at = datetime.now(UTC)
        for term in query.search_terms[: self._config.max_terms]:
            for page in range(1, self._config.max_pages + 1):
                params: dict[str, str | int] = {"q": term, "sort": "recent", "page": page}
                payload = await self._get_json(self.SEARCH, errors, params)
                jobs = self._parse_rows(HimalayasJob, payload, errors, "jobs")
                for job in jobs:
                    found.setdefault(job.guid, self._item(job, fetched_at))
                if len(jobs) < 20:
                    break
        return list(found.values())

    def _item(self, job: HimalayasJob, fetched_at: datetime) -> SourceItem:
        return SourceItem(
            source=self.name,
            source_item_id=job.guid,
            url=job.applicationLink,
            kind="job",
            title=job.title,
            raw_text=html_to_text(job.description),
            raw_payload=job.payload(),
            published_at=job.pubDate,
            fetched_at=fetched_at,
            org_name=job.companyName,
            location=", ".join(job.locationRestrictions) or "Worldwide",
            tags=[REMOTE, *job.categories, *([job.employmentType] if job.employmentType else [])],
            license_note=self.license_note,
        )


class WorkingNomadsConfig(AdapterConfig):
    pass


class WorkingNomadsJob(RawRecord):
    url: str
    title: str
    company_name: str | None = None
    description: str = ""
    category_name: str | None = None
    tags: str = ""
    location: str | None = None
    pub_date: datetime | None = None


class WorkingNomadsAdapter(HttpFeedAdapter):
    name = "workingnomads"
    license_note = "Working Nomads public job feed: link back to the job on Working Nomads."

    def __init__(
        self, client: httpx.AsyncClient, config: WorkingNomadsConfig, *, min_interval: float = 1.0
    ) -> None:
        super().__init__(client, min_interval)
        self._config = config

    async def _collect(self, query: SourceQuery, errors: list[str]) -> list[SourceItem]:
        payload = await self._get_json("https://www.workingnomads.com/api/exposed_jobs/", errors)
        fetched_at = datetime.now(UTC)
        return [
            SourceItem(
                source=self.name,
                source_item_id=job.url,
                url=job.url,
                kind="job",
                title=job.title,
                raw_text=html_to_text(job.description),
                raw_payload=job.payload(),
                published_at=job.pub_date,
                fetched_at=fetched_at,
                org_name=job.company_name,
                location=job.location,
                tags=[
                    REMOTE,
                    *(t.strip() for t in job.tags.split(",") if t.strip()),
                    *([job.category_name] if job.category_name else []),
                ],
                license_note=self.license_note,
            )
            for job in self._parse_rows(WorkingNomadsJob, payload, errors)
        ]


class WeWorkRemotelyConfig(AdapterConfig):
    feeds: list[str] = Field(
        default_factory=lambda: [
            "remote-jobs",
            "categories/remote-full-stack-programming-jobs",
            "categories/remote-back-end-programming-jobs",
            "categories/remote-devops-sysadmin-jobs",
        ],
        description="RSS feed paths under weworkremotely.com, without .rss.",
    )

    @field_validator("feeds")
    @classmethod
    def _paths(cls, feeds: list[str]) -> list[str]:
        bad = [f for f in feeds if not f or f.startswith(("/", "http")) or ".." in f]
        if bad:
            raise ValueError(f"feed paths are relative to weworkremotely.com: {bad}")
        return feeds


class WeWorkRemotelyAdapter(HttpFeedAdapter):
    name = "weworkremotely"
    license_note = (
        "We Work Remotely RSS feeds: link back to the job on weworkremotely.com and name it as "
        "the source; its terms page could not be read, so treat the feed as personal use only."
    )

    def __init__(
        self, client: httpx.AsyncClient, config: WeWorkRemotelyConfig, *, min_interval: float = 1.0
    ) -> None:
        super().__init__(client, min_interval)
        self._config = config

    async def _collect(self, query: SourceQuery, errors: list[str]) -> list[SourceItem]:
        found: dict[str, SourceItem] = {}
        fetched_at = datetime.now(UTC)
        for feed in self._config.feeds:
            response = await self._get(f"https://weworkremotely.com/{feed}.rss", errors)
            if response is None:
                continue
            for item in self._items(response.content, feed, errors, fetched_at):
                found.setdefault(item.source_item_id, item)
        return list(found.values())

    def _items(
        self, xml: bytes, feed: str, errors: list[str], fetched_at: datetime
    ) -> list[SourceItem]:
        try:
            # ElementTree resolves no external entities, so a hostile feed can't read files.
            root = ElementTree.fromstring(xml)
        except ElementTree.ParseError as exc:
            errors.append(f"{self.name}: unreadable RSS for {feed}: {exc}")
            return []
        items = []
        for node in root.iterfind("./channel/item"):
            fields = {child.tag: (child.text or "").strip() for child in node}
            link = fields.get("link") or fields.get("guid")
            if not link:
                continue
            # Titles read "Company: Role".
            company, _, title = fields.get("title", "").partition(": ")
            items.append(
                SourceItem(
                    source=self.name,
                    source_item_id=link,
                    url=link,
                    kind="job",
                    title=title or company,
                    raw_text=html_to_text(fields.get("description", "")),
                    raw_payload=dict[str, object](fields),
                    published_at=_rss_date(fields.get("pubDate")),
                    fetched_at=fetched_at,
                    org_name=company if title else None,
                    location=fields.get("region") or None,
                    tags=[REMOTE, *(s.strip() for s in fields.get("skills", "").split(","))],
                    license_note=self.license_note,
                )
            )
        return items


def _rss_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        logger.debug("unreadable RSS date %r", raw)
        return None
