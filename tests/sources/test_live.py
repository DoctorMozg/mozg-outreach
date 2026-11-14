import os
import stat
from datetime import UTC, datetime

import httpx
import pytest

from mozg_outreach.sources.feeds import ArbeitnowAdapter, ArbeitnowConfig
from mozg_outreach.sources.models import SourceQuery
from mozg_outreach.sources.telegram import SessionStore, TelegramAdapter, TelegramConfig
from mozg_outreach.sources.telegram_discovery import ChannelDiscovery, DiscoveryRequest

pytestmark = pytest.mark.live


async def test_arbeitnow_returns_real_listings() -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        adapter = ArbeitnowAdapter(client, ArbeitnowConfig(max_pages=1))
        result = await adapter.fetch(SourceQuery(limit=5), None)

    assert not result.errors
    assert result.items
    assert all(item.url and item.raw_text for item in result.items)


@pytest.mark.skipif(
    not os.environ.get("TELEGRAM_API_ID"), reason="needs Telegram credentials and a login"
)
async def test_telegram_discovers_and_reads_job_channels() -> None:
    config = TelegramConfig(discover=True, kind="job")
    adapter = TelegramAdapter(config)
    async with adapter.open_session() as session:
        found = await ChannelDiscovery(session).find(
            DiscoveryRequest(terms=["python"], known=set(), limit=2, now=datetime.now(UTC))
        )
    assert found.channels, found.concerns

    reader = TelegramAdapter(
        TelegramConfig(channels=[c.username for c in found.channels], kind="job")
    )
    first = await reader.fetch(SourceQuery(limit=5), None)
    second = await reader.fetch(SourceQuery(limit=5), first.cursor)

    assert not first.errors
    assert first.items
    assert all(i.url and i.url.startswith("https://t.me/") for i in first.items)
    assert len(second.items) <= 1, "a cursor read returns only posts newer than the first"
    session_file = SessionStore.from_env().session_file
    assert stat.S_IMODE(session_file.stat().st_mode) == 0o600
