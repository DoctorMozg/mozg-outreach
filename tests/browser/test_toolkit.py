from collections.abc import AsyncIterator
from typing import Any

import pytest
from claude_agent_sdk import SdkMcpTool
from playwright.async_api import Browser

from mozg_outreach.agents.toolkits import ToolkitUnavailable
from mozg_outreach.browser import BrowserError, BrowserService, BrowserSettings
from mozg_outreach.browser.toolkit import BrowserToolkit, browser_tools
from tests.browser.conftest import Site, ref, requires_chrome

type Tools = dict[str, SdkMcpTool[Any]]


@pytest.fixture
async def tools(browser: BrowserService) -> AsyncIterator[Tools]:
    async with browser.session() as session:
        yield {t.name: t for t in browser_tools(session)}


async def call(tools: Tools, name: str, **args: object) -> tuple[str, bool]:
    result = await tools[name].handler(args)
    return result["content"][0]["text"], bool(result.get("is_error"))


@requires_chrome
class TestTools:
    async def test_an_agent_opens_searches_and_goes_back(self, tools: Tools, site: Site) -> None:
        page, failed = await call(tools, "open", url=site.url())
        assert not failed
        assert page.startswith(f"URL: {site.url()}\nTitle: Directory\n\n")

        page, _ = await call(tools, "type", ref=ref(page, "Query"), text="acme", submit=True)
        assert "Results for acme" in page
        page, _ = await call(tools, "back")
        assert "Title: Directory" in page

    async def test_a_click_returns_the_next_page(self, tools: Tools, site: Site) -> None:
        page, _ = await call(tools, "open", url=site.url())
        page, _ = await call(tools, "click", ref=ref(page, "Second page"))
        assert "Title: Second" in page

    async def test_a_refused_page_is_an_error_result_not_a_crash(self, tools: Tools) -> None:
        text, failed = await call(tools, "open", url="http://10.0.0.1/admin")
        assert failed
        assert "not on the public internet" in text


@requires_chrome
async def test_session_names_its_tools_for_allowed_tools(browser: BrowserService) -> None:
    async with BrowserToolkit(browser).session() as session:
        assert session.tool_names == (
            "mcp__browser__open",
            "mcp__browser__click",
            "mcp__browser__type",
            "mcp__browser__back",
        )
        assert session.env == {"ENABLE_TOOL_SEARCH": "false"}
        assert session.server["name"] == "browser"


class BrokenBrowser(BrowserService):
    async def _started(self) -> Browser:
        raise BrowserError("could not start chrome: not installed")


async def test_a_browser_that_cannot_start_makes_the_toolkit_unavailable() -> None:
    toolkit = BrowserToolkit(BrokenBrowser(BrowserSettings()))
    with pytest.raises(ToolkitUnavailable, match="not installed"):
        async with toolkit.session():
            pass
