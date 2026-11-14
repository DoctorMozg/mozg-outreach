from datetime import UTC, datetime

import httpx
from pydantic import Field

from mozg_outreach.sources.base import (
    AdapterConfig,
    HttpFeedAdapter,
    RawRecord,
)
from mozg_outreach.sources.models import SourceItem, SourceQuery
from mozg_outreach.sources.text import html_to_text


class RemotiveConfig(AdapterConfig):
    category: str | None = None


class RemotiveJob(RawRecord):
    id: int
    url: str
    title: str
    company_name: str | None = None
    description: str = ""
    publication_date: datetime | None = None
    candidate_required_location: str | None = None
    category: str | None = None
    job_type: str | None = None
    tags: list[str] = Field(default_factory=list)


class RemotiveAdapter(HttpFeedAdapter):
    name = "remotive"
    license_note = (
        "Remotive API terms: link back and name Remotive as the source; listings are delayed "
        "24h; no re-submitting to job aggregators; collecting leads or sign-ups from it "
        "breaches the terms; poll at most ~4 times a day."
    )

    def __init__(
        self, client: httpx.AsyncClient, config: RemotiveConfig, *, min_interval: float = 1.0
    ) -> None:
        super().__init__(client, min_interval)
        self._config = config

    async def _collect(self, query: SourceQuery, errors: list[str]) -> list[SourceItem]:
        params: dict[str, str | int] = {}
        if self._config.category:
            params["category"] = self._config.category
        payload = await self._get_json("https://remotive.com/api/remote-jobs", errors, params)
        fetched_at = datetime.now(UTC)
        return [
            SourceItem(
                source=self.name,
                source_item_id=str(job.id),
                url=job.url,
                kind="job",
                title=job.title,
                raw_text=html_to_text(job.description),
                raw_payload=job.payload(),
                published_at=job.publication_date,
                fetched_at=fetched_at,
                org_name=(job.company_name or "").strip() or None,
                location=job.candidate_required_location,
                tags=[*job.tags, *([job.category] if job.category else [])],
                license_note=self.license_note,
            )
            for job in self._parse_rows(RemotiveJob, payload, errors, "jobs")
        ]


class RemoteOkConfig(AdapterConfig):
    pass


class RemoteOkJob(RawRecord):
    id: str | int
    position: str
    url: str
    company: str | None = None
    description: str = ""
    date: datetime | None = None
    location: str | None = None
    tags: list[str] = Field(default_factory=list)


class RemoteOkAdapter(HttpFeedAdapter):
    name = "remoteok"
    license_note = (
        "RemoteOK API terms: link back to the Remote OK URL (follow) and name Remote OK as "
        "the source; do not use the logo."
    )

    def __init__(
        self, client: httpx.AsyncClient, config: RemoteOkConfig, *, min_interval: float = 1.0
    ) -> None:
        super().__init__(client, min_interval)
        self._config = config

    async def _collect(self, query: SourceQuery, errors: list[str]) -> list[SourceItem]:
        payload = await self._get_json("https://remoteok.com/api", errors)
        # The first element is a legal notice, not a posting.
        rows = (
            [row for row in payload if not (isinstance(row, dict) and "legal" in row)]
            if isinstance(payload, list)
            else payload
        )
        fetched_at = datetime.now(UTC)
        return [
            SourceItem(
                source=self.name,
                source_item_id=str(job.id),
                url=job.url,
                kind="job",
                title=job.position,
                raw_text=html_to_text(job.description),
                raw_payload=job.payload(),
                published_at=job.date,
                fetched_at=fetched_at,
                org_name=job.company,
                location=job.location or None,
                tags=job.tags,
                license_note=self.license_note,
            )
            for job in self._parse_rows(RemoteOkJob, rows, errors)
        ]


class ArbeitnowConfig(AdapterConfig):
    max_pages: int = Field(default=1, ge=1, le=10)


class ArbeitnowJob(RawRecord):
    slug: str
    title: str
    url: str
    company_name: str | None = None
    description: str = ""
    location: str | None = None
    remote: bool = False
    tags: list[str] = Field(default_factory=list)
    job_types: list[str] = Field(default_factory=list)
    created_at: int | None = None


class ArbeitnowAdapter(HttpFeedAdapter):
    name = "arbeitnow"
    license_note = (
        "Arbeitnow free job board API: do not abuse, link back to arbeitnow.com, follow its "
        "terms of service."
    )

    def __init__(
        self, client: httpx.AsyncClient, config: ArbeitnowConfig, *, min_interval: float = 1.0
    ) -> None:
        super().__init__(client, min_interval)
        self._config = config

    async def _collect(self, query: SourceQuery, errors: list[str]) -> list[SourceItem]:
        fetched_at = datetime.now(UTC)
        items: list[SourceItem] = []
        for page in range(1, self._config.max_pages + 1):
            payload = await self._get_json(
                "https://www.arbeitnow.com/api/job-board-api", errors, {"page": page}
            )
            jobs = self._parse_rows(ArbeitnowJob, payload, errors, "data")
            items.extend(self._to_item(job, fetched_at) for job in jobs)
            if not jobs:
                break
        return items

    def _to_item(self, job: ArbeitnowJob, fetched_at: datetime) -> SourceItem:
        return SourceItem(
            source=self.name,
            source_item_id=job.slug,
            url=job.url,
            kind="job",
            title=job.title,
            raw_text=html_to_text(job.description),
            raw_payload=job.payload(),
            published_at=datetime.fromtimestamp(job.created_at, UTC) if job.created_at else None,
            fetched_at=fetched_at,
            org_name=job.company_name,
            location=job.location,
            tags=[*job.tags, *job.job_types, *(["remote"] if job.remote else [])],
            license_note=self.license_note,
        )
