import asyncio
import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from mozg_outreach.apply.browser import ApplyBrowser
from tests.apply.conftest import PAGE_DIR, ScriptedAnswerer, application
from tests.browser.conftest import requires_chrome

pytestmark = requires_chrome


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


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
