"""One shared headless browser; every agent session gets its own guarded, throwaway context."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import Literal

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Response,
    Route,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout
from pydantic import BaseModel, Field

from mozg_outreach.browser.guard import UrlGuard

logger = logging.getLogger(__name__)

# Most pages render within this after DOMContentLoaded; slow trackers never go idle.
SETTLE_MS = 3_000
# A link with target=_blank reports its tab shortly after the click returns.
POPUP_MS = 700


class BrowserSettings(BaseModel):
    channel: Literal["chrome", "chromium"] = Field(
        default="chrome", description="chrome drives the installed Google Chrome."
    )
    snapshot_chars: int = Field(default=40_000, ge=1_000)
    timeout_ms: int = Field(default=20_000, ge=1_000)


class BrowserError(RuntimeError):
    """A page action failed or was refused; the message is meant for the agent."""


class PageView(BaseModel):
    url: str
    title: str
    snapshot: str = Field(description="Accessibility tree with [ref=eN] handles for actions.")
    truncated: bool


class BrowserSession:
    """A page in its own context, opened only through the guard."""

    def __init__(self, context: BrowserContext, guard: UrlGuard, settings: BrowserSettings) -> None:
        self._context = context
        self._guard = guard
        self._settings = settings
        self._page: Page | None = None
        self._document: Response | None = None
        self.blocked: list[str] = []
        context.on("page", self._adopt)

    async def install(self) -> None:
        await self._context.route("**/*", self._route)
        self._context.set_default_timeout(self._settings.timeout_ms)

    async def open(self, url: str) -> PageView:
        problem = await self._guard.url_problem(url)
        if problem:
            raise BrowserError(problem)
        page = await self._current()
        await self._act(page.goto(url, wait_until="domcontentloaded"), f"open {url}")
        return await self.view()

    async def view(self) -> PageView:
        page = await self._current()
        await self._settle(page)
        await self._verify(page)
        snapshot = await page.aria_snapshot(mode="ai")
        cap = self._settings.snapshot_chars
        truncated = len(snapshot) > cap
        if truncated:
            snapshot = f"{snapshot[:cap]}\n[truncated at {cap} characters]"
        return PageView(
            url=page.url, title=await page.title(), snapshot=snapshot, truncated=truncated
        )

    async def click(self, ref: str) -> PageView:
        page = await self._current()
        popup = asyncio.ensure_future(self._context.wait_for_event("page", timeout=POPUP_MS))
        try:
            await self._act(page.get_by_ref(ref).click(), f"click {ref}")
        except BrowserError:
            popup.cancel()
            raise
        with contextlib.suppress(PlaywrightTimeout):
            await popup  # the "page" listener has already switched to it
        return await self.view()

    async def fill(self, ref: str, text: str, *, submit: bool = False) -> PageView:
        """Types into a field; `submit` presses Enter, which only GET forms survive."""
        page = await self._current()
        field = page.get_by_ref(ref)
        await self._act(field.fill(text), f"fill {ref}")
        if submit:
            await self._act(field.press("Enter"), f"submit {ref}")
        return await self.view()

    async def back(self) -> PageView:
        page = await self._current()
        await self._act(page.go_back(wait_until="domcontentloaded"), "go back")
        return await self.view()

    async def close(self) -> None:
        await self._context.close()

    async def _current(self) -> Page:
        if self._page is None or self._page.is_closed():
            self._adopt(await self._context.new_page())
        assert self._page is not None
        return self._page

    def _adopt(self, page: Page) -> None:
        # A link that opens a new tab moves the session there.
        self._page = page
        self._document = None
        page.on("response", self._on_response)

    def _on_response(self, response: Response) -> None:
        frame = response.frame
        if response.request.is_navigation_request() and frame == frame.page.main_frame:
            self._document = response

    async def _route(self, route: Route) -> None:
        request = route.request
        problem = await self._guard.request_problem(request.method, request.url)
        if problem:
            self.blocked.append(f"{request.method} {request.url}: {problem}")
            logger.info("blocked %s %s: %s", request.method, request.url, problem)
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    async def _act(self, action: Awaitable[object], what: str) -> None:
        try:
            await action
        except PlaywrightError as exc:
            message = str(exc).splitlines()[0]
            raise BrowserError(f"could not {what}: {message}") from exc

    async def _settle(self, page: Page) -> None:
        try:
            await page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
        except PlaywrightTimeout:
            logger.debug("%s kept loading; reading it as it is", page.url)

    async def _verify(self, page: Page) -> None:
        """Redirects and DNS changes can land past the request check; nothing private is shown."""
        if page.url.startswith("chrome-error://"):
            reason = self.blocked[-1] if self.blocked else "the page failed to load"
            await self._act(page.go_back(wait_until="domcontentloaded"), "go back")
            raise BrowserError(f"{reason}; back on the previous page")
        problem = await self._guard.url_problem(page.url) if page.url != "about:blank" else None
        if problem is None and self._document is not None:
            server = await self._document.server_addr()
            problem = self._guard.address_problem(server["ipAddress"]) if server else None
        if problem:
            await page.goto("about:blank")
            raise BrowserError(problem)


class BrowserService:
    """Starts Chrome on first use and shares it; contexts keep sessions apart."""

    def __init__(self, settings: BrowserSettings, guard: UrlGuard | None = None) -> None:
        self._settings = settings
        self._guard = guard or UrlGuard()
        self._lock = asyncio.Lock()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    @asynccontextmanager
    async def session(self) -> AsyncIterator[BrowserSession]:
        browser = await self._started()
        context = await browser.new_context(accept_downloads=False, service_workers="block")
        session = BrowserSession(context, self._guard, self._settings)
        await session.install()
        try:
            yield session
        finally:
            await session.close()

    async def stop(self) -> None:
        async with self._lock:
            if self._browser is not None:
                await self._browser.close()
            if self._playwright is not None:
                await self._playwright.stop()
            self._browser = self._playwright = None

    async def _started(self) -> Browser:
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                if self._playwright is None:
                    self._playwright = await async_playwright().start()
                try:
                    self._browser = await self._playwright.chromium.launch(
                        channel=self._settings.channel, headless=True
                    )
                except PlaywrightError as exc:
                    raise BrowserError(
                        f"could not start {self._settings.channel}: {str(exc).splitlines()[0]}"
                    ) from exc
                logger.info("started headless %s", self._settings.channel)
            return self._browser
