"""Job listings from API feeds and Telegram, which need no scout agent."""

import asyncio
import logging
from datetime import date, timedelta

from pydantic import BaseModel

from mozg_outreach.pipelines.job.listings import SourcedListing
from mozg_outreach.pipelines.job.models import ScoutedListing
from mozg_outreach.sources.base import SourceAdapter
from mozg_outreach.sources.models import FetchResult, SourceItem, SourceQuery

logger = logging.getLogger(__name__)

SUMMARY_CHARS = 2000


class FeedOutcome(BaseModel):
    listings: list[SourcedListing]
    cursors: dict[str, str | None]
    concerns: list[str]


class FeedRequest(BaseModel):
    keywords: list[str]
    recency_days: int
    today: date
    cursors: dict[str, str | None]


class JobFeeds:
    def __init__(self, adapters: list[SourceAdapter]) -> None:
        self._adapters = adapters

    async def collect(self, request: FeedRequest) -> FeedOutcome:
        query = SourceQuery(keywords=request.keywords, limit=200)
        results = await asyncio.gather(
            *(a.fetch(query, request.cursors.get(a.name)) for a in self._adapters)
        )
        oldest = request.today - timedelta(days=request.recency_days)
        outcome = FeedOutcome(listings=[], cursors={}, concerns=[])
        for adapter, result in zip(self._adapters, results, strict=True):
            outcome.cursors[adapter.name] = result.cursor
            outcome.concerns += [f"{adapter.name}: {e}" for e in result.errors]
            outcome.listings += self._listings(result, oldest)
        logger.info("feeds gave %d job listings", len(outcome.listings))
        return outcome

    @staticmethod
    def _listings(result: FetchResult, oldest: date) -> list[SourcedListing]:
        listings = []
        for item in result.items:
            if item.kind != "job":
                continue
            if item.published_at and item.published_at.date() < oldest:
                continue
            listing = to_listing(item)
            if listing:
                listings.append(SourcedListing(source=item.source, listing=listing))
        return listings


def to_listing(item: SourceItem) -> ScoutedListing | None:
    """None when the item lacks a link or an employer, which every listing needs."""
    title, company = item.title or "", item.org_name or ""
    if not company and "|" in title:
        # Hacker News posts follow "Company | Role | Location | ...".
        parts = [p.strip() for p in title.split("|")]
        company, title = parts[0], parts[1] if len(parts) > 1 else ""
    if not (item.url and title.strip() and company.strip()):
        return None
    place = f"{item.location or ''} {' '.join(item.tags)}"
    return ScoutedListing(
        title=title.strip(),
        company=company.strip(),
        location_string=item.location,
        remote_status_raw="Remote" if "remote" in place.casefold() else None,
        url=item.url,
        posted_date=item.published_at.date().isoformat() if item.published_at else None,
        salary_string=None,
        summary_snippet=item.raw_text[:SUMMARY_CHARS],
    )
