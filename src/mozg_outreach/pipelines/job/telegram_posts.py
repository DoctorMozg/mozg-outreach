"""Telegram job posts as listings: a skill filter, then an agent reads each post and its links."""

import asyncio
import logging
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.pipelines.biz.models import CONCERNS_DESCRIPTION
from mozg_outreach.pipelines.job.feeds import FeedOutcome
from mozg_outreach.pipelines.job.listings import SourcedListing
from mozg_outreach.pipelines.job.models import ScoutedListing
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.sources.base import SourceAdapter
from mozg_outreach.sources.models import SourceItem, SourceQuery

logger = logging.getLogger(__name__)

POSTS_PER_CHANNEL = 40
MAX_POSTS = 120
BATCH = 20
PARALLEL_BATCHES = 3
POST_CHARS = 3000
UNPARSED = "Telegram posts not parsed"
REMOTE_LABELS = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site"}


class PostInput(BaseModel):
    id: str
    channel: str
    posted_at: str | None
    text: str
    links: list[str]


class ParserInput(BaseModel):
    posts: list[PostInput] = Field(min_length=1)


class ParsedPost(BaseModel):
    id: str
    is_job: bool
    title: str | None
    company: str | None
    location: str | None
    remote: Literal["remote", "hybrid", "onsite"] | None
    salary: str | None
    apply_url: str | None = Field(description="One of the post's links, copied exactly.")
    reason: str


class ParserResult(BaseModel):
    posts: list[ParsedPost]
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


class TelegramRequest(BaseModel):
    skills: list[str] = Field(description="Kept posts name at least one; works in any language.")
    oldest: date
    cursor: str | None


def post_links(item: SourceItem) -> list[str]:
    links = item.raw_payload.get("links")
    return [str(link) for link in links] if isinstance(links, list) else []


def to_scouted(item: SourceItem, parsed: ParsedPost, concerns: list[str]) -> ScoutedListing | None:
    """Keep only what the post shows: the company must appear in its text, the link in its links."""
    if not (parsed.is_job and parsed.title and parsed.title.strip() and item.url):
        return None
    company = (parsed.company or "").strip()
    if company and company.casefold() not in item.raw_text.casefold():
        concerns.append(f"{item.source_item_id}: dropped company '{company}', not in the post.")
        company = ""
    links = post_links(item)
    apply_url = parsed.apply_url if parsed.apply_url in links else None
    if parsed.apply_url and apply_url is None:
        concerns.append(f"{item.source_item_id}: dropped apply link, not one of the post's links.")
    footer = f"\n\nLinks: {' '.join(links)}" if links else ""
    return ScoutedListing(
        title=parsed.title.strip(),
        company=company,
        location_string=parsed.location,
        remote_status_raw=REMOTE_LABELS.get(parsed.remote or ""),
        url=item.url,
        posted_date=item.published_at.date().isoformat() if item.published_at else None,
        salary_string=parsed.salary,
        summary_snippet=item.raw_text + footer,
        apply_url=apply_url,
    )


class TelegramPosts:
    def __init__(self, steps: PipelineSteps) -> None:
        self._steps = steps

    async def collect(self, adapter: SourceAdapter, request: TelegramRequest) -> FeedOutcome:
        query = SourceQuery(keywords=request.skills, limit=POSTS_PER_CHANNEL)
        fetched = await adapter.fetch(query, request.cursor)
        fresh = [
            i
            for i in fetched.items
            if i.published_at is None or i.published_at.date() >= request.oldest
        ]
        recent = fresh[:MAX_POSTS]
        concerns = [f"{adapter.name}: {e}" for e in fetched.errors]
        if len(fresh) > MAX_POSTS:
            concerns.append(f"telegram: parsed the newest {MAX_POSTS} of {len(fresh)} posts.")
        failures = len(concerns)
        listings = await self.parse(recent, concerns)
        parsed_all = not any(c.startswith(UNPARSED) for c in concerns[failures:])
        logger.info("telegram: %d posts gave %d listings", len(recent), len(listings))
        # Keeping the old cursor after a failed batch makes the next run read those posts again.
        cursor = fetched.cursor if parsed_all else None
        return FeedOutcome(listings=listings, cursors={adapter.name: cursor}, concerns=concerns)

    async def parse(self, items: list[SourceItem], concerns: list[str]) -> list[SourcedListing]:
        batches = [items[i : i + BATCH] for i in range(0, len(items), BATCH)]
        gate = asyncio.Semaphore(PARALLEL_BATCHES)

        async def run(batch: list[SourceItem]) -> ParserResult | None:
            async with gate:
                return await self._parse_batch(batch, concerns)

        results = await asyncio.gather(*(run(b) for b in batches))
        by_id = {item.source_item_id: item for item in items}
        listings: list[SourcedListing] = []
        for result in results:
            if result is None:
                continue
            concerns += result.concerns
            for parsed in result.posts:
                item = by_id.get(parsed.id)
                if item is None:
                    concerns.append(f"Telegram parser returned an unknown post id '{parsed.id}'.")
                    continue
                scouted = to_scouted(item, parsed, concerns)
                if scouted:
                    listings.append(SourcedListing(source=item.source, listing=scouted))
        return listings

    async def _parse_batch(
        self, batch: list[SourceItem], concerns: list[str]
    ) -> ParserResult | None:
        payload = ParserInput(
            posts=[
                PostInput(
                    id=item.source_item_id,
                    channel=str(item.raw_payload.get("channel", "")),
                    posted_at=item.published_at.isoformat() if item.published_at else None,
                    text=item.raw_text[:POST_CHARS],
                    links=post_links(item),
                )
                for item in batch
            ]
        )
        try:
            return await self._steps.run("job", "telegram-post-parser", payload, ParserResult)
        except (AgentRunError, AgentOutputError) as exc:
            # One failed batch costs its posts this run; the cursor still moves past them.
            logger.warning("telegram post parser failed on %d posts: %s", len(batch), exc)
            concerns.append(f"{UNPARSED} ({len(batch)} posts): {exc}")
            return None
