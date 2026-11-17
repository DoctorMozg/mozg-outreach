"""Job listings from API feeds and Telegram, which need no scout agent."""

import asyncio
import logging
from datetime import date, timedelta

from pydantic import BaseModel

from mozg_outreach.pipelines.seeker.listings import SourcedListing
from mozg_outreach.pipelines.seeker.models import ScoutedListing, SkillRules
from mozg_outreach.pipelines.seeker.skills import SkillMatcher
from mozg_outreach.sources.base import SourceAdapter
from mozg_outreach.sources.models import FetchResult, SourceItem, SourceQuery

logger = logging.getLogger(__name__)

# The longest Greenhouse posting measured was about 15,000 characters.
DESCRIPTION_CHARS = 16000
# Whole feeds come back unfiltered; the skill check below picks what screening would keep.
FEED_LIMIT = 1000


class FeedOutcome(BaseModel):
    listings: list[SourcedListing]
    cursors: dict[str, str | None]
    concerns: list[str]


class FeedRequest(BaseModel):
    skills: SkillRules
    recency_days: int
    today: date
    cursors: dict[str, str | None]


class JobFeeds:
    def __init__(self, adapters: list[SourceAdapter]) -> None:
        self._adapters = adapters

    async def collect(self, request: FeedRequest) -> FeedOutcome:
        # Title phrases missed most fitting jobs ("Senior AI Frameworks Engineer" is in no
        # posting), so feeds are filtered by the must-have skills screening checks anyway.
        rules = request.skills
        terms = [*rules.must_have_skills, *(s for g in rules.must_have_or_groups for s in g)]
        query = SourceQuery(limit=FEED_LIMIT, search_terms=list(dict.fromkeys(terms)))
        matcher = SkillMatcher(request.skills)
        results = await asyncio.gather(
            *(a.fetch(query, request.cursors.get(a.name)) for a in self._adapters)
        )
        oldest = request.today - timedelta(days=request.recency_days)
        outcome = FeedOutcome(listings=[], cursors={}, concerns=[])
        for adapter, result in zip(self._adapters, results, strict=True):
            outcome.cursors[adapter.name] = result.cursor
            outcome.concerns += [f"{adapter.name}: {e}" for e in result.errors]
            listings = self._listings(result, oldest, matcher)
            logger.info(
                "%s: %d of %d items name a must-have skill",
                adapter.name,
                len(listings),
                len(result.items),
            )
            outcome.listings += listings
        logger.info("feeds gave %d job listings", len(outcome.listings))
        return outcome

    @staticmethod
    def _listings(result: FetchResult, oldest: date, matcher: SkillMatcher) -> list[SourcedListing]:
        listings = []
        for item in result.items:
            if item.kind != "job":
                continue
            if item.published_at and item.published_at.date() < oldest:
                continue
            if not matcher.match(f"{item.title or ''}\n{item.raw_text}").gate_passed:
                continue
            listing = to_listing(item)
            if listing:
                listings.append(SourcedListing(source=item.source, kind="job", listing=listing))
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
        summary_snippet=item.raw_text[:DESCRIPTION_CHARS],
    )
