import json
from datetime import UTC, datetime

import httpx
import pytest

from mozg_outreach.sources.models import SourceQuery
from mozg_outreach.sources.remote_boards import (
    HimalayasAdapter,
    HimalayasConfig,
    WeWorkRemotelyAdapter,
    WeWorkRemotelyConfig,
    WorkingNomadsAdapter,
    WorkingNomadsConfig,
)

# Shapes copied from the live responses on 2026-11-17, trimmed.
HIMALAYAS_JOB = {
    "title": "Senior C++ Engineer",
    "companyName": "Alien Intellect",
    "description": "<p>Modern <strong>C++20</strong> inference runtime.</p>",
    "applicationLink": "https://himalayas.app/companies/alien/jobs/senior-cpp?utm_source=api",
    "guid": "https://himalayas.app/companies/alien/jobs/senior-cpp",
    "pubDate": "1794869843",
    "locationRestrictions": [],
    "employmentType": "Full Time",
    "categories": ["Software-Engineer"],
}
WWR_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
  <title>Lemon.io: Senior Python Developer</title>
  <region>Anywhere in the World</region>
  <skills>Python, Django, Back-End Dev</skills>
  <description>&lt;p&gt;Build Python services.&lt;/p&gt;</description>
  <pubDate>Wed, 11 Nov 2026 16:41:48 +0000</pubDate>
  <link>https://weworkremotely.com/remote-jobs/lemon-io-senior-python-developer</link>
</item>
</channel></rss>"""
NOMAD_JOB = {
    "url": "https://www.workingnomads.com/job/go/2027058/",
    "title": "Backend Engineer (Python)",
    "description": "<p>Python and Postgres.</p>",
    "company_name": "Marker",
    "category_name": "Development",
    "tags": "python,postgres",
    "location": "Europe",
    "pub_date": "2026-11-16T11:56:02-05:00",
}


def client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


class TestHimalayas:
    async def test_searches_each_term_and_keeps_the_apply_link_unchanged(self) -> None:
        asked: list[dict[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            asked.append(dict(request.url.params))
            jobs = [HIMALAYAS_JOB] if request.url.params["q"] == "C++" else []
            return httpx.Response(200, json={"jobs": jobs})

        adapter = HimalayasAdapter(
            client(httpx.MockTransport(handler)), HimalayasConfig(), min_interval=0
        )
        result = await adapter.fetch(SourceQuery(search_terms=["Python", "C++"]), None)

        assert [(a["q"], a["page"]) for a in asked] == [("Python", "1"), ("C++", "1")]
        [item] = result.items
        assert item.url == HIMALAYAS_JOB["applicationLink"]
        assert (item.org_name, item.location) == ("Alien Intellect", "Worldwide")
        assert "remote" in item.tags
        assert item.raw_text == "Modern C++20 inference runtime."
        assert item.published_at == datetime.fromtimestamp(1794869843, UTC)

    async def test_without_terms_nothing_is_searched(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError(f"unexpected request {request.url}")

        adapter = HimalayasAdapter(
            client(httpx.MockTransport(handler)), HimalayasConfig(), min_interval=0
        )
        result = await adapter.fetch(SourceQuery(), None)
        assert result.items == []
        assert result.errors == ["himalayas: no search terms, so nothing was searched"]


async def test_we_work_remotely_splits_company_from_role_and_dedupes_feeds() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=WWR_RSS)

    config = WeWorkRemotelyConfig(feeds=["remote-jobs", "categories/back-end"])
    adapter = WeWorkRemotelyAdapter(client(httpx.MockTransport(handler)), config, min_interval=0)
    result = await adapter.fetch(SourceQuery(), None)

    [item] = result.items
    assert (item.org_name, item.title) == ("Lemon.io", "Senior Python Developer")
    assert item.raw_text == "Build Python services."
    assert item.location == "Anywhere in the World"
    assert item.published_at == datetime(2026, 11, 11, 16, 41, 48, tzinfo=UTC)


def test_we_work_remotely_feed_paths_stay_on_its_site() -> None:
    with pytest.raises(ValueError, match=r"relative to weworkremotely\.com"):
        WeWorkRemotelyConfig(feeds=["https://evil.example/feed"])


async def test_working_nomads_reads_its_json_feed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps([NOMAD_JOB]))

    adapter = WorkingNomadsAdapter(
        client(httpx.MockTransport(handler)), WorkingNomadsConfig(), min_interval=0
    )
    result = await adapter.fetch(SourceQuery(), None)

    [item] = result.items
    assert (item.title, item.org_name, item.url) == (
        "Backend Engineer (Python)",
        "Marker",
        NOMAD_JOB["url"],
    )
    assert item.tags[:3] == ["remote", "python", "postgres"]
