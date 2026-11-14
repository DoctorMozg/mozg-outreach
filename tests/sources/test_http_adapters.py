from datetime import UTC, datetime
from typing import ClassVar

import httpx

from mozg_outreach.sources.ats import (
    AshbyAdapter,
    BoardsConfig,
    CompaniesConfig,
    GreenhouseAdapter,
    LeverAdapter,
)
from mozg_outreach.sources.base import SourceAdapter
from mozg_outreach.sources.feeds import (
    ArbeitnowAdapter,
    ArbeitnowConfig,
    RemoteOkAdapter,
    RemoteOkConfig,
    RemotiveAdapter,
    RemotiveConfig,
)
from mozg_outreach.sources.hn import HnConfig, HnHiringAdapter
from mozg_outreach.sources.models import SourceQuery
from tests.sources.conftest import WebFactory, load_fixture

ALL = SourceQuery()


class TestGreenhouse:
    async def test_normalizes_postings(self, web: WebFactory) -> None:
        client, fake = web({"/v1/boards/acme/jobs": load_fixture("greenhouse")})
        adapter = GreenhouseAdapter(client, BoardsConfig(boards=["acme"]), min_interval=0)

        result = await adapter.fetch(ALL, None)

        assert len(result.items) == 3
        assert not result.errors
        item = result.items[0]
        assert item.source == "greenhouse"
        assert item.source_item_id.startswith("acme/")
        assert item.kind == "job"
        assert item.title
        assert item.org_name
        assert "<" not in item.raw_text
        assert "&lt;" not in item.raw_text
        assert item.published_at is not None
        assert item.published_at.tzinfo is not None
        assert fake.requests[0].url.params["content"] == "true"

    async def test_http_error_goes_to_errors_and_other_boards_still_load(
        self, web: WebFactory
    ) -> None:
        client, _ = web({"/v1/boards/acme/jobs": load_fixture("greenhouse")})
        adapter = GreenhouseAdapter(client, BoardsConfig(boards=["gone", "acme"]), min_interval=0)

        result = await adapter.fetch(ALL, None)

        assert len(result.items) == 3
        assert result.errors == [
            "greenhouse: HTTP 404 for https://boards-api.greenhouse.io/v1/boards/gone/jobs"
        ]

    async def test_cursor_round_trip_skips_seen_postings(self, web: WebFactory) -> None:
        client, _ = web({"/v1/boards/acme/jobs": load_fixture("greenhouse")})
        adapter = GreenhouseAdapter(client, BoardsConfig(boards=["acme"]), min_interval=0)

        first = await adapter.fetch(ALL, None)
        second = await adapter.fetch(ALL, first.cursor)

        assert first.cursor is not None
        assert second.items == []
        assert second.cursor == first.cursor

    async def test_invalid_cursor_is_reported_and_ignored(self, web: WebFactory) -> None:
        client, _ = web({"/v1/boards/acme/jobs": load_fixture("greenhouse")})
        adapter = GreenhouseAdapter(client, BoardsConfig(boards=["acme"]), min_interval=0)

        result = await adapter.fetch(ALL, "not-a-date")

        assert len(result.items) == 3
        assert result.errors == ["greenhouse: ignored invalid cursor"]

    async def test_keyword_filter_applies(self, web: WebFactory) -> None:
        client, _ = web({"/v1/boards/acme/jobs": load_fixture("greenhouse")})
        adapter = GreenhouseAdapter(client, BoardsConfig(boards=["acme"]), min_interval=0)

        result = await adapter.fetch(SourceQuery(keywords=["no-such-word-xyz"]), None)

        assert result.items == []


class TestLever:
    async def test_normalizes_postings(self, web: WebFactory) -> None:
        client, fake = web({"/v0/postings/acme": load_fixture("lever")})
        adapter = LeverAdapter(client, CompaniesConfig(companies=["acme"]), min_interval=0)

        result = await adapter.fetch(ALL, None)

        item = result.items[0]
        assert len(result.items) == 2
        assert item.source_item_id.startswith("acme/")
        assert item.url
        assert item.url.startswith("https://jobs.lever.co/")
        assert item.location
        assert item.raw_text
        assert item.published_at is not None
        assert item.published_at.year >= 2025
        assert fake.requests[0].url.params["mode"] == "json"


class TestAshby:
    async def test_normalizes_postings(self, web: WebFactory) -> None:
        client, _ = web({"/posting-api/job-board/acme": load_fixture("ashby")})
        adapter = AshbyAdapter(client, BoardsConfig(boards=["acme"]), min_interval=0)

        result = await adapter.fetch(ALL, None)

        titles = {i.title for i in result.items}
        assert "Engineering Manager - EU" in titles
        item = next(i for i in result.items if i.title == "Engineering Manager - EU")
        assert item.location == "Remote - European Union"
        assert item.tags[:2] == ["Engineering", "EMEA Engineering"]
        assert item.published_at == datetime(2024, 3, 4, 14, 29, 8, 532000, tzinfo=UTC)

    async def test_unlisted_postings_are_dropped(self, web: WebFactory) -> None:
        board = {
            "jobs": [{"id": "x", "title": "Hidden", "jobUrl": "https://e.com/x", "isListed": False}]
        }
        client, _ = web({"/posting-api/job-board/acme": board})
        adapter = AshbyAdapter(client, BoardsConfig(boards=["acme"]), min_interval=0)

        assert (await adapter.fetch(ALL, None)).items == []

    async def test_malformed_rows_are_counted_not_fatal(self, web: WebFactory) -> None:
        board = {"jobs": [{"title": "no id or url"}]}
        client, _ = web({"/posting-api/job-board/acme": board})
        adapter = AshbyAdapter(client, BoardsConfig(boards=["acme"]), min_interval=0)

        result = await adapter.fetch(ALL, None)

        assert result.items == []
        assert result.errors == ["ashby: skipped 1 malformed items"]


class TestRemotive:
    async def test_normalizes_and_labels_terms(self, web: WebFactory) -> None:
        client, fake = web({"/api/remote-jobs": load_fixture("remotive")})
        adapter = RemotiveAdapter(client, RemotiveConfig(category="software-dev"), min_interval=0)

        result = await adapter.fetch(ALL, None)

        item = next(i for i in result.items if i.source_item_id == "2091137")
        assert item.title == "Senior Data Engineer"
        assert item.published_at == datetime(2026, 11, 11, 18, 39, 59, tzinfo=UTC)
        assert item.license_note
        assert "lead" in item.license_note
        assert fake.requests[0].url.params["category"] == "software-dev"


class TestRemoteOk:
    async def test_legal_notice_element_is_skipped(self, web: WebFactory) -> None:
        client, _ = web({"/api": load_fixture("remoteok")})
        adapter = RemoteOkAdapter(client, RemoteOkConfig(), min_interval=0)

        result = await adapter.fetch(ALL, None)

        assert len(result.items) == 2
        assert not result.errors
        assert all(i.title for i in result.items)
        assert result.items[0].license_note
        assert "link back" in result.items[0].license_note


class TestArbeitnow:
    async def test_normalizes_epoch_dates_and_tags(self, web: WebFactory) -> None:
        client, fake = web({"/api/job-board-api": load_fixture("arbeitnow")})
        adapter = ArbeitnowAdapter(client, ArbeitnowConfig(max_pages=1), min_interval=0)

        result = await adapter.fetch(ALL, None)

        assert len(result.items) == 3
        item = result.items[0]
        assert item.published_at is not None
        assert item.published_at.tzinfo is not None
        assert item.org_name
        assert item.source_item_id == "strategy-intern-berlin-294496"
        assert fake.requests[0].url.params["page"] == "1"

    async def test_stops_paging_on_an_empty_page(self, web: WebFactory) -> None:
        client, fake = web({"/api/job-board-api": {"data": []}})
        adapter = ArbeitnowAdapter(client, ArbeitnowConfig(max_pages=3), min_interval=0)

        await adapter.fetch(ALL, None)

        assert len(fake.requests) == 1


class TestHnWhoIsHiring:
    ROUTES: ClassVar[dict[str, object]] = {
        "/api/v1/search_by_date": load_fixture("hn_stories"),
        "/api/v1/items/50338728": load_fixture("hn_thread"),
    }

    async def test_picks_latest_hiring_story_not_wants_to_be_hired(self, web: WebFactory) -> None:
        client, fake = web(self.ROUTES)
        adapter = HnHiringAdapter(client, HnConfig(), min_interval=0)

        await adapter.fetch(ALL, None)

        assert fake.requests[-1].url.path == "/api/v1/items/50338728"

    async def test_top_level_comments_become_listings(self, web: WebFactory) -> None:
        client, _ = web(self.ROUTES)
        adapter = HnHiringAdapter(client, HnConfig(), min_interval=0)

        result = await adapter.fetch(ALL, None)

        assert len(result.items) == 2  # the deleted comment has no text
        item = result.items[0]
        assert item.url == f"https://news.ycombinator.com/item?id={item.source_item_id}"
        assert item.title
        assert "|" in item.title
        assert "<p>" not in item.raw_text
        assert "children" not in item.raw_payload

    async def test_missing_story_is_reported(self, web: WebFactory) -> None:
        client, _ = web({"/api/v1/search_by_date": {"hits": []}})
        adapter = HnHiringAdapter(client, HnConfig(), min_interval=0)

        result = await adapter.fetch(ALL, None)

        assert result.items == []
        assert len(result.errors) == 1
        assert "no 'Ask HN: Who is hiring?' story" in result.errors[0]


class TestFailures:
    async def test_server_error_returns_errors_not_exception(self, web: WebFactory) -> None:
        client, _ = web({"/api/remote-jobs": 503})
        adapter = RemotiveAdapter(client, RemotiveConfig(), min_interval=0)

        result = await adapter.fetch(ALL, None)

        assert result.items == []
        assert result.errors == ["remotive: HTTP 503 for https://remotive.com/api/remote-jobs"]

    async def test_transport_failure_returns_errors(self) -> None:
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
        adapter = RemotiveAdapter(client, RemotiveConfig(), min_interval=0)

        result = await adapter.fetch(ALL, None)

        assert result.errors == ["remotive: ConnectError for https://remotive.com/api/remote-jobs"]

    async def test_invalid_json_returns_errors(self) -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>"))
        )
        adapter = RemoteOkAdapter(client, RemoteOkConfig(), min_interval=0)

        result = await adapter.fetch(ALL, None)

        assert result.errors == ["remoteok: JSONDecodeError for https://remoteok.com/api"]
        assert result.items == []


def test_adapters_satisfy_the_protocol(web: WebFactory) -> None:
    client, _ = web({})
    assert isinstance(HnHiringAdapter(client, HnConfig()), SourceAdapter)
