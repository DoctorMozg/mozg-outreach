"""Find public Telegram channels that post jobs: search by term, then vet each in Python."""

import asyncio
import logging
import re
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from mozg_outreach.sources.telegram import (
    ChannelInfo,
    ChannelMessage,
    TelegramReadError,
    TelegramSession,
    channel_username,
)

logger = logging.getLogger(__name__)

MAX_QUERIES = 8
PAUSE_SECONDS = 1.0
SEARCH_LIMIT = 20
MAX_CANDIDATES = 30
SAMPLE_POSTS = 20
ACTIVE_DAYS = 14
MIN_JOB_SHARE = 0.3
# Words job posts use in English and Russian, the two languages job channels mostly post in.
JOB_MARKERS = re.compile(
    r"vacanc|hiring|we.re looking|salary|requirements|apply|remote|full[- ]time|"
    r"#jobs?\b|вакан|ищем|зарплат|з/п|требовани|удал[её]н|резюме|опыт работы",
    re.IGNORECASE,
)


class FoundChannel(BaseModel):
    username: str
    title: str
    subscribers: int | None
    last_post: datetime
    job_share: float = Field(description="Share of recent posts that read like job posts.")
    query: str = Field(description="The search that found it.")


class DiscoveryRequest(BaseModel):
    terms: list[str] = Field(min_length=1, description="Skills or roles; each becomes a query.")
    known: set[str] = Field(description="Usernames already listed or excluded, casefolded.")
    limit: int = Field(ge=1)
    now: datetime


class DiscoveryResult(BaseModel):
    channels: list[FoundChannel]
    concerns: list[str]


async def _pause() -> None:
    # Spacing calls keeps an unofficial client clear of Telegram's flood limits.
    await asyncio.sleep(PAUSE_SECONDS)


def job_share(posts: list[ChannelMessage]) -> float:
    texts = [p.text for p in posts if p.text.strip()]
    return sum(1 for t in texts if JOB_MARKERS.search(t)) / len(texts) if texts else 0.0


class ChannelDiscovery:
    def __init__(self, session: TelegramSession) -> None:
        self._session = session

    async def find(self, request: DiscoveryRequest) -> DiscoveryResult:
        concerns: list[str] = []
        candidates = await self._search(request, concerns)
        found: list[FoundChannel] = []
        for query, info in candidates[:MAX_CANDIDATES]:
            checked = await self._vet(query, info, request.now, concerns)
            if checked is not None:
                found.append(checked)
        found.sort(key=lambda c: (c.job_share, c.subscribers or 0), reverse=True)
        kept = found[: request.limit]
        logger.info("telegram discovery: %d candidates, %d kept", len(candidates), len(kept))
        return DiscoveryResult(channels=kept, concerns=concerns)

    async def _search(
        self, request: DiscoveryRequest, concerns: list[str]
    ) -> list[tuple[str, ChannelInfo]]:
        seen = set(request.known)
        candidates: list[tuple[str, ChannelInfo]] = []
        for term in request.terms[:MAX_QUERIES]:
            query = f"{term} jobs"
            try:
                infos = await self._session.search_channels(query, SEARCH_LIMIT)
            except TelegramReadError as exc:
                concerns.append(f"Telegram search failed: {exc}")
                continue
            for info in infos:
                key = channel_username(info.username).casefold()
                if info.broadcast and key not in seen:
                    seen.add(key)
                    candidates.append((query, info))
            await _pause()
        candidates.sort(key=lambda pair: pair[1].subscribers or 0, reverse=True)
        return candidates

    async def _vet(
        self, query: str, info: ChannelInfo, now: datetime, concerns: list[str]
    ) -> FoundChannel | None:
        try:
            posts = await self._session.messages(info.username, 0, SAMPLE_POSTS)
        except TelegramReadError as exc:
            concerns.append(f"Skipped channel @{info.username}: {exc}")
            return None
        finally:
            await _pause()
        if not posts:
            return None
        last_post = max(p.date for p in posts)
        share = job_share(posts)
        if now - last_post > timedelta(days=ACTIVE_DAYS) or share < MIN_JOB_SHARE:
            logger.debug("telegram discovery: @%s dropped (share %.2f)", info.username, share)
            return None
        return FoundChannel(
            username=info.username,
            title=info.title,
            subscribers=info.subscribers,
            last_post=last_post,
            job_share=round(share, 2),
            query=query,
        )
