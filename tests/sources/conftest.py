import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from mozg_outreach.sources.telegram import ChannelInfo, ChannelMessage, TelegramReadError

FIXTURES = Path(__file__).parent / "fixtures"

type Routes = dict[str, object]


def load_fixture(name: str) -> object:
    parsed: object = json.loads((FIXTURES / f"{name}.json").read_text())
    return parsed


class FakeWeb:
    """Serves recorded JSON by URL path and records the requests it saw."""

    def __init__(self, routes: Routes) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = self.routes.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"error": "not found"})
        if isinstance(body, int):
            return httpx.Response(body)
        return httpx.Response(200, json=body)


type WebFactory = Callable[[Routes], tuple[httpx.AsyncClient, FakeWeb]]


@pytest.fixture
def web() -> WebFactory:
    def make(routes: Routes) -> tuple[httpx.AsyncClient, FakeWeb]:
        fake = FakeWeb(routes)
        return httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)), fake

    return make


NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)
JOB = "Hiring a Python developer, remote, salary 5k"
CHAT = "Anyone at the meetup tonight?"


def posts(*texts: str, age_days: int = 1) -> list[ChannelMessage]:
    date = NOW - timedelta(days=age_days)
    return [ChannelMessage(id=i + 1, text=t, date=date) for i, t in enumerate(texts)]


def channel(username: str, subscribers: int = 1000, *, broadcast: bool = True) -> ChannelInfo:
    return ChannelInfo(
        username=username, title=username.title(), broadcast=broadcast, subscribers=subscribers
    )


class SearchSession:
    """Answers channel searches per query and serves each channel's recent posts."""

    def __init__(
        self, results: dict[str, list[ChannelInfo]], history: dict[str, list[ChannelMessage]]
    ) -> None:
        self.results = results
        self.history = history
        self.queries: list[str] = []
        self.reads: list[tuple[str, int]] = []

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def messages(self, channel: str, min_id: int, limit: int) -> list[ChannelMessage]:
        self.reads.append((channel, min_id))
        if channel not in self.history:
            raise TelegramReadError(f"ChannelPrivateError reading {channel}")
        return self.history[channel][:limit]

    async def search_channels(self, query: str, limit: int) -> list[ChannelInfo]:
        self.queries.append(query)
        if query == "broken jobs":
            raise TelegramReadError("FloodWaitError searching 'broken jobs'")
        return self.results.get(query, [])[:limit]
