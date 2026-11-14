import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

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
