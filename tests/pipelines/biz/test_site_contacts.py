import httpx
import pytest

from mozg_outreach.pipelines.biz.site_contacts import SiteSweep


def cloudflare(address: str, key: int = 0x5A) -> str:
    return bytes([key, *(b ^ key for b in address.encode())]).hex()


HOME = """<html><body>
<nav><a href="/contact-us">Get in touch</a> <a href="/about/leadership">Our team</a>
<a href="https://acme.ae/privacy">Privacy</a> <a href="https://other.example/contact">Partner</a>
<a href="/blog/post-1">Blog</a></nav>
<script>var x = "tracker@sentry.io";</script>
<img src="logo@2x.png">
</body></html>"""
PAGES = {
    "/": HOME,
    "/contact-us": f"""<p>Write to <a href="mailto:Info@Acme.ae?subject=Hi">us</a> or
        <a href="/cdn-cgi/l/email-protection"
           data-cfemail="{cloudflare("sales@acme.ae")}">[email]</a>.
        Our designer: studio@agency.example</p>""",
    "/privacy": """<p>Data protection officer: dpo [at] acme (dot) ae</p>
        <form><input name="email"><button>Subscribe</button></form>""",
    "/contact": """<form action="/send"><input name="name">
        <textarea name="msg"></textarea></form>""",
    "/about/leadership": """<h2>Dr Sara Khan</h2><p>Chief Executive Officer</p>
        <a href="https://ae.linkedin.com/in/sara-khan-12/?trk=x">Sara Khan</a>""",
}


def handler(request: httpx.Request) -> httpx.Response:
    assert request.url.host == "acme.ae", f"left the company site: {request.url}"
    body = PAGES.get(request.url.path)
    if body is None:
        return httpx.Response(404, text="missing")
    return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})


@pytest.fixture
async def sweep() -> SiteSweep:
    return SiteSweep(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_reads_published_addresses_and_profiles_from_the_companys_own_pages(
    sweep: SiteSweep,
) -> None:
    found = await sweep.read("acme.ae")

    assert sorted((e.address, e.source_url) for e in found.emails) == [
        ("dpo@acme.ae", "https://acme.ae/privacy"),
        ("info@acme.ae", "https://acme.ae/contact-us"),
        ("sales@acme.ae", "https://acme.ae/contact-us"),
    ]
    assert all(e.kind == "published" for e in found.emails)
    [profile] = found.profiles
    assert profile.url == "https://www.linkedin.com/in/sara-khan-12"
    assert (profile.text, profile.source_url) == ("Sara Khan", "https://acme.ae/about/leadership")
    assert "https://acme.ae/blog/post-1" not in found.pages
    assert found.contact_form == "https://acme.ae/contact", "a newsletter box isn't a contact form"
    assert found.concerns == []


async def test_an_unreachable_site_is_a_concern_not_an_error() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    sweep = SiteSweep(httpx.AsyncClient(transport=httpx.MockTransport(down)))
    found = await sweep.read("acme.ae")
    assert found.emails == []
    assert found.concerns == ["Couldn't read https://acme.ae/ to look for published contacts."]


async def test_reads_at_most_the_page_budget() -> None:
    seen: list[str] = []

    def many(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        links = "".join(f'<a href="/team-{i}">Team {i}</a>' for i in range(20))
        return httpx.Response(200, text=links, headers={"content-type": "text/html"})

    await SiteSweep(httpx.AsyncClient(transport=httpx.MockTransport(many)), max_pages=3).read(
        "acme.ae"
    )
    assert len(seen) == 4


async def test_keeps_a_parent_groups_address_and_reads_each_page_once() -> None:
    # HealthHub's privacy page lists its inbox on the Al-Futtaim group domain.
    seen: list[str] = []

    def brand(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/privacy":
            return httpx.Response(301, headers={"location": "https://healthhubgroup.ae/privacy/"})
        pages = {
            "/": '<a href="/privacy">Privacy</a> <a href="/privacy/">Privacy policy</a>',
            "/privacy/": "Write to Info.HealthHub@group.com or ads@tracker.example",
        }
        body = pages.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(brand), follow_redirects=True)
    found = await SiteSweep(client).read("healthhubgroup.ae")

    assert [e.address for e in found.emails] == ["info.healthhub@group.com"]
    assert found.pages.count("https://healthhubgroup.ae/privacy/") == 1
