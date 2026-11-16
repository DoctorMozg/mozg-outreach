"""The `browser` toolkit: read-only page tools an agent drives through one guarded session."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Annotated, Any

from claude_agent_sdk import SdkMcpTool, ToolAnnotations, create_sdk_mcp_server, tool

from mozg_outreach.agents.toolkits import ToolkitSession, ToolkitUnavailable, mcp_tool_name
from mozg_outreach.browser.service import BrowserError, BrowserService, BrowserSession, PageView

logger = logging.getLogger(__name__)

TOOLKIT = "browser"
# Above the snapshot cap, so Claude Code keeps a page inline instead of saving it to a file
# the agent has no tool to read.
RESULT_CHARS = 60_000
# Deferred tool search would hide these tools behind a lookup the agents can't make.
TOOLKIT_ENV = {"ENABLE_TOOL_SEARCH": "false"}

type ToolResult = dict[str, Any]  # the SDK's MCP result shape: content list plus is_error


def render(view: PageView) -> str:
    note = "\n(The page was cut short; use what is shown.)" if view.truncated else ""
    return f"URL: {view.url}\nTitle: {view.title}{note}\n\n{view.snapshot}"


class BrowserToolkit:
    """Gives each agent run its own browser context; the guard keeps it on public GET pages."""

    name = TOOLKIT

    def __init__(self, service: BrowserService) -> None:
        self._service = service

    @asynccontextmanager
    async def session(self) -> AsyncIterator[ToolkitSession]:
        async with AsyncExitStack() as stack:
            try:
                browser = await stack.enter_async_context(self._service.session())
            except BrowserError as exc:
                raise ToolkitUnavailable(str(exc)) from exc
            tools = browser_tools(browser)
            yield ToolkitSession(
                server=create_sdk_mcp_server(TOOLKIT, tools=tools),
                tool_names=tuple(mcp_tool_name(TOOLKIT, t.name) for t in tools),
                env=TOOLKIT_ENV,
            )


def browser_tools(browser: BrowserSession) -> list[SdkMcpTool[Any]]:
    annotations = ToolAnnotations(readOnlyHint=True, maxResultSizeChars=RESULT_CHARS)

    async def answer(action: Awaitable[PageView]) -> ToolResult:
        try:
            view = await action
        except BrowserError as exc:
            # The agent reads the reason and picks another page; the run goes on.
            return {"content": [{"type": "text", "text": str(exc)}], "is_error": True}
        return {"content": [{"type": "text", "text": render(view)}]}

    def define(
        name: str, description: str, schema: dict[str, Any]
    ) -> Callable[[Callable[[Any], Awaitable[ToolResult]]], SdkMcpTool[Any]]:
        return tool(name, description, schema, annotations=annotations)

    @define(
        "open",
        "Open a public web page and return its accessibility tree. Elements carry [ref=eN] "
        "handles for click and type. Use it for pages WebFetch can't read: ones built by "
        "JavaScript, directories with filters, search results inside a site.",
        {"url": Annotated[str, "Absolute http or https URL"]},
    )
    async def open_page(args: dict[str, str]) -> ToolResult:
        return await answer(browser.open(args["url"]))

    @define(
        "click",
        "Click an element by its ref from the last page view: a link, tab, 'show more' or "
        "pagination button. Returns the page after the click.",
        {"ref": Annotated[str, "An element ref such as e12"]},
    )
    async def click(args: dict[str, str]) -> ToolResult:
        return await answer(browser.click(args["ref"]))

    @define(
        "type",
        "Type text into a search box or filter field by its ref. With submit true, press "
        "Enter to run the search. Forms that send data are blocked; browsing is read-only.",
        {
            "ref": Annotated[str, "An element ref such as e7"],
            "text": str,
            "submit": Annotated[bool, "Press Enter after typing"],
        },
    )
    async def type_text(args: dict[str, Any]) -> ToolResult:
        return await answer(
            browser.fill(args["ref"], str(args["text"]), submit=bool(args["submit"]))
        )

    @define("back", "Go back to the previous page.", {})
    async def back(_args: dict[str, Any]) -> ToolResult:
        return await answer(browser.back())

    return [open_page, click, type_text, back]
