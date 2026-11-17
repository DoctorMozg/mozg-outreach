import asyncio
import threading
import time
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from mozg_outreach.apply import browser as apply_browser
from mozg_outreach.apply.browser import ApplyBrowser
from tests.apply.conftest import PAGE_DIR, ScriptedAnswerer, application
from tests.browser.conftest import requires_chrome

pytestmark = requires_chrome


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        if self.path == "/never-loads.png":
            time.sleep(20)  # an ad or tracker that keeps the page's load event from firing
            return
        super().do_GET()


@pytest.fixture(scope="module")
def pages() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(PAGE_DIR)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


async def test_fills_in_the_profile_and_reports_the_confirmation(
    pages: str, tmp_path: Path
) -> None:
    browser = ApplyBrowser(tmp_path / "profile", headless=True)
    sent: asyncio.Queue[str] = asyncio.Queue()
    try:
        report = await browser.prepare(
            7, f"{pages}/spa_form.html", application(), ScriptedAnswerer({}), sent.put_nowait
        )
        assert [f.label for f in report.filled] == ["Full name", "Email"]

        confirmed = await asyncio.wait_for(sent.get(), timeout=10)

        assert confirmed == f"{pages}/spa_form.html"
        assert (tmp_path / "profile").is_dir()
    finally:
        await browser.stop()


async def test_a_page_that_never_finishes_loading_is_filled_anyway(
    pages: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(apply_browser, "SETTLE_MS", 300)
    browser = ApplyBrowser(tmp_path / "profile", headless=True)
    try:
        report = await asyncio.wait_for(
            browser.prepare(
                8, f"{pages}/slow_assets.html", application(), ScriptedAnswerer({}), print
            ),
            timeout=10,
        )

        assert [f.label for f in report.filled] == ["Full name", "Email"]
    finally:
        await browser.stop()
