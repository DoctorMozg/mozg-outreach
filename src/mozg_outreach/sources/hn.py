import logging
from datetime import UTC, datetime

import httpx

from mozg_outreach.sources.base import AdapterConfig, HttpFeedAdapter, RawRecord
from mozg_outreach.sources.models import SourceItem, SourceQuery
from mozg_outreach.sources.text import html_to_text

logger = logging.getLogger(__name__)

_STORY_TITLE_PREFIX = "Ask HN: Who is hiring?"
_TITLE_MAX_CHARS = 200


class HnConfig(AdapterConfig):
    pass


class HnStory(RawRecord):
    objectID: str
    title: str = ""
    created_at_i: int = 0


class HnComment(RawRecord):
    id: int
    author: str | None = None
    text: str | None = None
    created_at: datetime | None = None


class HnHiringAdapter(HttpFeedAdapter):
    name = "hn_whoishiring"
    license_note = (
        "Hacker News posts are user content fetched via the public Algolia HN API; link back "
        "to the item and do not republish."
    )

    def __init__(
        self, client: httpx.AsyncClient, config: HnConfig, *, min_interval: float = 0.5
    ) -> None:
        super().__init__(client, min_interval)
        self._config = config

    async def _collect(self, query: SourceQuery, errors: list[str]) -> list[SourceItem]:
        story_id = await self._latest_story_id(errors)
        if story_id is None:
            return []
        thread = await self._get_json(f"https://hn.algolia.com/api/v1/items/{story_id}", errors)
        fetched_at = datetime.now(UTC)
        items: list[SourceItem] = []
        # Only top-level comments are listings; deeper ones are replies.
        for comment in self._parse_rows(HnComment, thread, errors, "children"):
            if not comment.text:
                continue
            items.append(self._to_item(comment, fetched_at))
        logger.info("hn_whoishiring: thread %s has %d listings", story_id, len(items))
        return items

    async def _latest_story_id(self, errors: list[str]) -> str | None:
        payload = await self._get_json(
            "https://hn.algolia.com/api/v1/search_by_date",
            errors,
            {"tags": "story,author_whoishiring", "hitsPerPage": 10},
        )
        stories = self._parse_rows(HnStory, payload, errors, "hits")
        hiring = [s for s in stories if s.title.startswith(_STORY_TITLE_PREFIX)]
        if not hiring:
            if payload is not None:
                errors.append(f"{self.name}: no '{_STORY_TITLE_PREFIX}' story found")
            return None
        return max(hiring, key=lambda s: s.created_at_i).objectID

    def _to_item(self, comment: HnComment, fetched_at: datetime) -> SourceItem:
        text = html_to_text(comment.text or "")
        first_line = text.split("\n", 1)[0]
        return SourceItem(
            source=self.name,
            source_item_id=str(comment.id),
            url=f"https://news.ycombinator.com/item?id={comment.id}",
            kind="job",
            title=first_line[:_TITLE_MAX_CHARS],
            raw_text=text,
            raw_payload=comment.model_dump(mode="json", exclude={"children"}),
            published_at=comment.created_at,
            fetched_at=fetched_at,
            author=comment.author,
            license_note=self.license_note,
        )
