import asyncio
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from mozg_outreach.sources.models import FetchResult, SourceItem, SourceQuery

logger = logging.getLogger(__name__)

USER_AGENT = "mozg-outreach/0.1 (local job search tool)"


@runtime_checkable
class SourceAdapter(Protocol):
    @property
    def name(self) -> str: ...

    async def fetch(self, query: SourceQuery, cursor: str | None) -> FetchResult: ...


class RateLimiter:
    """Keeps a minimum interval between consecutive calls."""

    def __init__(self, min_interval: float) -> None:
        self._min_interval = min_interval
        self._last_call: float | None = None
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            if self._last_call is not None:
                remaining = self._min_interval - (time.monotonic() - self._last_call)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            self._last_call = time.monotonic()


def matches_keywords(item: SourceItem, keywords: list[str]) -> bool:
    if not keywords:
        return True
    haystack = f"{item.title or ''}\n{item.raw_text}".casefold()
    return any(keyword.casefold() in haystack for keyword in keywords if keyword.strip())


def newest_published(items: list[SourceItem]) -> datetime | None:
    dates = [item.published_at for item in items if item.published_at]
    return max(dates) if dates else None


def select_items(items: list[SourceItem], query: SourceQuery) -> list[SourceItem]:
    """Keyword and since filters, newest first, capped at the query limit."""
    kept = [
        item
        for item in items
        if matches_keywords(item, query.keywords)
        and (query.since is None or item.published_at is None or item.published_at > query.since)
    ]
    kept.sort(
        key=lambda item: item.published_at.timestamp() if item.published_at else 0.0, reverse=True
    )
    return kept[: query.limit]


class RawRecord(BaseModel):
    """Upstream JSON row: known fields typed, the rest kept for raw_payload."""

    model_config = ConfigDict(extra="allow")

    def payload(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class AdapterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HttpFeedAdapter(ABC):
    """Shared plumbing for JSON-over-HTTP sources: rate limit, error capture, cursor."""

    name: str

    def __init__(self, client: httpx.AsyncClient, min_interval: float) -> None:
        self._client = client
        self._limiter = RateLimiter(min_interval)

    @abstractmethod
    async def _collect(self, query: SourceQuery, errors: list[str]) -> list[SourceItem]: ...

    async def fetch(self, query: SourceQuery, cursor: str | None) -> FetchResult:
        errors: list[str] = []
        since = self._effective_since(query.since, cursor, errors)
        items = await self._collect(query, errors)
        selected = select_items(items, query.model_copy(update={"since": since}))
        newest = newest_published(items)
        logger.info(
            "%s: fetched %d items, kept %d, %d errors",
            self.name,
            len(items),
            len(selected),
            len(errors),
        )
        return FetchResult(
            items=selected,
            cursor=newest.isoformat() if newest else cursor,
            errors=errors,
        )

    def _effective_since(
        self, since: datetime | None, cursor: str | None, errors: list[str]
    ) -> datetime | None:
        if cursor is None:
            return since
        try:
            cursor_time = datetime.fromisoformat(cursor)
        except ValueError:
            errors.append(f"{self.name}: ignored invalid cursor")
            return since
        return cursor_time if since is None or cursor_time > since else since

    async def _get_json(
        self, url: str, errors: list[str], params: dict[str, str | int] | None = None
    ) -> object | None:
        await self._limiter.wait()
        try:
            response = await self._client.get(
                url, params=params, headers={"User-Agent": USER_AGENT}
            )
            response.raise_for_status()
            payload: object = response.json()
        except httpx.HTTPStatusError as exc:
            errors.append(f"{self.name}: HTTP {exc.response.status_code} for {url}")
        except (httpx.TransportError, ValueError) as exc:
            errors.append(f"{self.name}: {type(exc).__name__} for {url}")
        else:
            return payload
        logger.warning("%s: request failed for %s", self.name, url)
        return None

    def _parse_rows[T: RawRecord](
        self, model: type[T], payload: object, errors: list[str], key: str | None = None
    ) -> list[T]:
        if payload is None:  # the request failure is already in errors
            return []
        rows = payload.get(key) if key and isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            errors.append(f"{self.name}: expected a list of postings")
            return []
        parsed: list[T] = []
        skipped = 0
        for row in rows:
            try:
                parsed.append(model.model_validate(row))
            except ValidationError:
                skipped += 1
        if skipped:
            errors.append(f"{self.name}: skipped {skipped} malformed items")
        return parsed
