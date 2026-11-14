import httpx
import pytest

from mozg_outreach.sources.feeds import ArbeitnowAdapter, ArbeitnowConfig
from mozg_outreach.sources.models import SourceQuery

pytestmark = pytest.mark.live


async def test_arbeitnow_returns_real_listings() -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        adapter = ArbeitnowAdapter(client, ArbeitnowConfig(max_pages=1))
        result = await adapter.fetch(SourceQuery(limit=5), None)

    assert not result.errors
    assert result.items
    assert all(item.url and item.raw_text for item in result.items)
