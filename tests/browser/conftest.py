import ipaddress
import shutil
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from mozg_outreach.browser import BrowserService, BrowserSettings, UrlGuard
from mozg_outreach.browser.guard import IpAddress, Resolver

LOOPBACK = ipaddress.ip_address("127.0.0.1")

PAGES = {
    "/": """<title>Directory</title><h1>Directory</h1>
<a href="/second">Second page</a>
<a href="/second" target="_blank">Second in a new tab</a>
<form action="/search"><label>Query <input name="q"></label></form>
<form action="/apply" method="post"><label>Name <input name="name"></label>
<button>Apply</button></form>""",
    "/second": "<title>Second</title><h1>Second page</h1>",
    "/secret": "<title>Secret</title><p>SECRET TOKEN</p>",
}

requires_chrome = pytest.mark.skipif(
    shutil.which("google-chrome") is None, reason="needs an installed Google Chrome"
)


class Site:
    def __init__(self, port: int) -> None:
        self.port = port
        self.requests: list[str] = []

    def url(self, path: str = "/", host: str = "127.0.0.1") -> str:
        return f"http://{host}:{self.port}{path}"


def _handler(site_ref: list[Site]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            site_ref[0].requests.append(f"GET {self.path}")
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            if parts.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", query["to"][0])
                self.end_headers()
                return
            if parts.path == "/search":
                body = f"<title>Results</title><h1>Results for {query['q'][0]}</h1>"
            else:
                body = PAGES.get(parts.path, "<h1>Not found</h1>")
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            site_ref[0].requests.append(f"POST {self.path}")
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    return Handler


@pytest.fixture(scope="session")
def site() -> Iterator[Site]:
    holder: list[Site] = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(holder))
    holder.append(Site(server.server_address[1]))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield holder[0]
    server.shutdown()


@pytest.fixture(autouse=True)
def fresh_requests(site: Site) -> None:
    site.requests.clear()


def ref(snapshot: str, text: str) -> str:
    """The [ref=eN] handle on the first snapshot line mentioning `text`."""
    line = next(line for line in snapshot.splitlines() if text in line and "[ref=" in line)
    return line.split("[ref=", 1)[1].split("]", 1)[0]


def fake_dns(table: dict[str, str]) -> Resolver:
    async def resolve(host: str) -> list[str]:
        return [table.get(host, "127.0.0.1")]

    return resolve


def only_loopback(ip: IpAddress) -> bool:
    return ip == LOOPBACK


@pytest.fixture
async def browser() -> AsyncIterator[BrowserService]:
    """Treats this machine as the internet, so the local test site stands in for real pages."""
    service = BrowserService(BrowserSettings(), UrlGuard(fake_dns({}), only_loopback))
    yield service
    await service.stop()
