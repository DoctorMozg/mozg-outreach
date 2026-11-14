from datetime import UTC, datetime
from urllib.parse import quote

import httpx
from pydantic import Field

from mozg_outreach.sources.base import (
    AdapterConfig,
    HttpFeedAdapter,
    RawRecord,
)
from mozg_outreach.sources.models import SourceItem, SourceQuery
from mozg_outreach.sources.text import html_to_text, unescape_html_to_text


class BoardsConfig(AdapterConfig):
    boards: list[str] = Field(min_length=1)


class NamedRef(RawRecord):
    name: str | None = None


class GreenhouseJob(RawRecord):
    id: int
    title: str
    absolute_url: str
    content: str = ""
    company_name: str | None = None
    first_published: datetime | None = None
    updated_at: datetime | None = None
    location: NamedRef | None = None
    departments: list[NamedRef] = Field(default_factory=list)


class GreenhouseAdapter(HttpFeedAdapter):
    name = "greenhouse"
    license_note = (
        "Greenhouse public job board API: free, no auth; postings belong to each company."
    )

    def __init__(
        self, client: httpx.AsyncClient, config: BoardsConfig, *, min_interval: float = 0.5
    ) -> None:
        super().__init__(client, min_interval)
        self._config = config

    async def _collect(self, query: SourceQuery, errors: list[str]) -> list[SourceItem]:
        fetched_at = datetime.now(UTC)
        items: list[SourceItem] = []
        for board in self._config.boards:
            url = f"https://boards-api.greenhouse.io/v1/boards/{quote(board)}/jobs"
            payload = await self._get_json(url, errors, {"content": "true"})
            for job in self._parse_rows(GreenhouseJob, payload, errors, "jobs"):
                items.append(
                    SourceItem(
                        source=self.name,
                        source_item_id=f"{board}/{job.id}",
                        url=job.absolute_url,
                        kind="job",
                        title=job.title,
                        raw_text=unescape_html_to_text(job.content),
                        raw_payload=job.payload(),
                        published_at=job.first_published or job.updated_at,
                        fetched_at=fetched_at,
                        org_name=job.company_name or board,
                        location=job.location.name if job.location else None,
                        tags=[d.name for d in job.departments if d.name],
                        license_note=self.license_note,
                    )
                )
        return items


class CompaniesConfig(AdapterConfig):
    companies: list[str] = Field(min_length=1)


class LeverList(RawRecord):
    text: str = ""
    content: str = ""


class LeverCategories(RawRecord):
    location: str | None = None
    team: str | None = None
    commitment: str | None = None


class LeverPosting(RawRecord):
    id: str
    text: str
    hostedUrl: str
    createdAt: int | None = None
    descriptionPlain: str = ""
    additionalPlain: str = ""
    lists: list[LeverList] = Field(default_factory=list)
    categories: LeverCategories | None = None


class LeverAdapter(HttpFeedAdapter):
    name = "lever"
    license_note = "Lever public postings API: free, no auth; postings belong to each company."

    def __init__(
        self, client: httpx.AsyncClient, config: CompaniesConfig, *, min_interval: float = 0.5
    ) -> None:
        super().__init__(client, min_interval)
        self._config = config

    async def _collect(self, query: SourceQuery, errors: list[str]) -> list[SourceItem]:
        fetched_at = datetime.now(UTC)
        items: list[SourceItem] = []
        for company in self._config.companies:
            url = f"https://api.lever.co/v0/postings/{quote(company)}"
            payload = await self._get_json(url, errors, {"mode": "json"})
            for post in self._parse_rows(LeverPosting, payload, errors):
                cats = post.categories
                items.append(
                    SourceItem(
                        source=self.name,
                        source_item_id=f"{company}/{post.id}",
                        url=post.hostedUrl,
                        kind="job",
                        title=post.text,
                        raw_text=self._body(post),
                        raw_payload=post.payload(),
                        published_at=(
                            datetime.fromtimestamp(post.createdAt / 1000, UTC)
                            if post.createdAt
                            else None
                        ),
                        fetched_at=fetched_at,
                        org_name=company,
                        location=cats.location if cats else None,
                        tags=[t for t in (cats.team, cats.commitment) if t] if cats else [],
                        license_note=self.license_note,
                    )
                )
        return items

    @staticmethod
    def _body(post: LeverPosting) -> str:
        sections = [f"{item.text}\n{html_to_text(item.content)}" for item in post.lists]
        return "\n\n".join(s for s in [post.descriptionPlain, *sections, post.additionalPlain] if s)


class AshbyJob(RawRecord):
    id: str
    title: str
    jobUrl: str
    descriptionPlain: str = ""
    publishedAt: datetime | None = None
    location: str | None = None
    department: str | None = None
    team: str | None = None
    employmentType: str | None = None
    isListed: bool = True


class AshbyAdapter(HttpFeedAdapter):
    name = "ashby"
    license_note = "Ashby public job posting API: free, no auth; postings belong to each company."

    def __init__(
        self, client: httpx.AsyncClient, config: BoardsConfig, *, min_interval: float = 0.5
    ) -> None:
        super().__init__(client, min_interval)
        self._config = config

    async def _collect(self, query: SourceQuery, errors: list[str]) -> list[SourceItem]:
        fetched_at = datetime.now(UTC)
        items: list[SourceItem] = []
        for board in self._config.boards:
            url = f"https://api.ashbyhq.com/posting-api/job-board/{quote(board)}"
            payload = await self._get_json(url, errors, {"includeCompensation": "true"})
            for job in self._parse_rows(AshbyJob, payload, errors, "jobs"):
                if not job.isListed:
                    continue
                items.append(
                    SourceItem(
                        source=self.name,
                        source_item_id=f"{board}/{job.id}",
                        url=job.jobUrl,
                        kind="job",
                        title=job.title,
                        raw_text=job.descriptionPlain,
                        raw_payload=job.payload(),
                        published_at=job.publishedAt,
                        fetched_at=fetched_at,
                        org_name=board,
                        location=job.location,
                        tags=[t for t in (job.department, job.team, job.employmentType) if t],
                        license_note=self.license_note,
                    )
                )
        return items
