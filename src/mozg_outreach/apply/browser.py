"""A visible Chrome on the app's own profile, where the applicant reviews and submits forms."""

import asyncio
import contextlib
import logging
import re
from pathlib import Path

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from mozg_outreach.apply.answers import Answerer
from mozg_outreach.apply.desk import OnSent
from mozg_outreach.apply.filler import Application, FormFiller
from mozg_outreach.apply.form import FormError
from mozg_outreach.apply.models import FillReport

logger = logging.getLogger(__name__)

CONFIRMATION = re.compile(
    r"thank(s| you) for (applying|your application|submitting)|"
    r"application (was |has been )?(submitted|received|sent)|"
    r"we('ve| have) received your application",
    re.IGNORECASE,
)
WATCH_SECONDS = 2.0
OPEN_MS = 30_000
# Ads, trackers and lazy images can keep a page from ever finishing; the form is there long before.
SETTLE_MS = 10_000


class ApplyBrowser:
    """One headed persistent context; each item gets its own tab, kept open for the applicant."""

    def __init__(self, profile: Path, channel: str = "chrome", *, headless: bool = False) -> None:
        self._profile = profile
        self._channel = channel
        self._headless = headless  # tests only; the applicant needs to see the form
        self._lock = asyncio.Lock()
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._pages: dict[int, Page] = {}
        self._watchers: dict[int, asyncio.Task[None]] = {}

    async def prepare(
        self, item_id: int, url: str, application: Application, answerer: Answerer, on_sent: OnSent
    ) -> FillReport:
        await self.close(item_id)
        context = await self._started()
        page = await context.new_page()
        self._pages[item_id] = page
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=OPEN_MS)
        except PlaywrightError as exc:
            raise FormError(f"could not open {url}: {str(exc).splitlines()[0]}") from exc
        try:
            await page.wait_for_load_state("load", timeout=SETTLE_MS)
        except PlaywrightTimeoutError:
            logger.info(
                "item %d: %s still loading after %d ms; filling anyway", item_id, url, SETTLE_MS
            )
        report = await FormFiller(answerer).fill(page, application)
        await page.bring_to_front()
        self._watchers[item_id] = asyncio.create_task(self._watch(item_id, page, on_sent))
        return report

    async def close(self, item_id: int) -> None:
        watcher = self._watchers.pop(item_id, None)
        if watcher is not None:
            watcher.cancel()
        page = self._pages.pop(item_id, None)
        if page is not None and not page.is_closed():
            await page.close()

    async def stop(self) -> None:
        for item_id in list(self._pages):
            await self.close(item_id)
        async with self._lock:
            if self._context is not None:
                with contextlib.suppress(PlaywrightError):
                    await self._context.close()
            if self._playwright is not None:
                await self._playwright.stop()
            self._context = self._playwright = None

    async def _started(self) -> BrowserContext:
        async with self._lock:
            if self._context is None:
                if self._playwright is None:
                    self._playwright = await async_playwright().start()
                self._profile.mkdir(parents=True, exist_ok=True)
                try:
                    context = await self._playwright.chromium.launch_persistent_context(
                        self._profile,
                        channel=self._channel,
                        headless=self._headless,
                        no_viewport=True,
                    )
                except PlaywrightError as exc:
                    raise FormError(
                        f"could not start {self._channel}: {str(exc).splitlines()[0]}"
                    ) from exc
                context.on("close", self._forget)
                self._context = context
                logger.info("opened the application browser on %s", self._profile)
            return self._context

    def _forget(self, _context: BrowserContext) -> None:
        # The applicant closed the window; the next application opens a new one.
        self._context = None
        self._pages.clear()

    async def _watch(self, item_id: int, page: Page, on_sent: OnSent) -> None:
        """Polls the tab for a confirmation, which also catches forms that never navigate."""
        while not page.is_closed():
            await asyncio.sleep(WATCH_SECONDS)
            if any(CONFIRMATION.search(text) for text in await _frame_texts(page)):
                logger.info("item %d: confirmation page at %s", item_id, page.url)
                on_sent(page.url)
                return


async def _frame_texts(page: Page) -> list[str]:
    """Body text of every frame: an embedded form confirms inside its iframe."""
    texts: list[str] = []
    for frame in page.frames:
        try:
            texts.append(await frame.locator("body").inner_text(timeout=1_000))
        except PlaywrightError:
            continue  # mid-navigation or detached; the next poll reads it again
    return texts
