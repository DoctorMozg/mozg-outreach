import pytest

from mozg_outreach.browser import BrowserError, BrowserService, BrowserSettings, UrlGuard
from tests.browser.conftest import Site, fake_dns, only_loopback, ref, requires_chrome

pytestmark = requires_chrome


class TestBrowsing:
    async def test_open_reads_the_page_with_refs(self, browser: BrowserService, site: Site) -> None:
        async with browser.session() as session:
            view = await session.open(site.url())
        assert view.title == "Directory"
        assert 'heading "Directory"' in view.snapshot
        assert "[ref=e" in view.snapshot
        assert not view.truncated

    async def test_click_follows_a_link_and_back_returns(
        self, browser: BrowserService, site: Site
    ) -> None:
        async with browser.session() as session:
            home = await session.open(site.url())
            second = await session.click(ref(home.snapshot, 'link "Second page"'))
            again = await session.back()
        assert second.title == "Second"
        assert again.title == "Directory"

    async def test_a_new_tab_becomes_the_page(self, browser: BrowserService, site: Site) -> None:
        async with browser.session() as session:
            home = await session.open(site.url())
            view = await session.click(ref(home.snapshot, "Second in a new tab"))
        assert view.title == "Second"

    async def test_a_get_search_form_submits(self, browser: BrowserService, site: Site) -> None:
        async with browser.session() as session:
            home = await session.open(site.url())
            view = await session.fill(
                ref(home.snapshot, 'textbox "Query"'), "law firms", submit=True
            )
        assert 'heading "Results for law firms"' in view.snapshot

    async def test_a_long_page_is_cut(self, site: Site) -> None:
        service = BrowserService(
            BrowserSettings(snapshot_chars=1_000), UrlGuard(fake_dns({}), only_loopback)
        )
        try:
            async with service.session() as session:
                await session.open(site.url())
                session_view = await session.view()
        finally:
            await service.stop()
        assert len(session_view.snapshot) < 2_000


class TestGuard:
    async def test_this_machine_is_off_limits(self, site: Site) -> None:
        service = BrowserService(BrowserSettings())
        try:
            async with service.session() as session:
                with pytest.raises(BrowserError, match="not on the public internet"):
                    await session.open(site.url())
        finally:
            await service.stop()
        assert site.requests == []

    async def test_a_post_form_never_reaches_the_server(
        self, browser: BrowserService, site: Site
    ) -> None:
        async with browser.session() as session:
            home = await session.open(site.url())
            with pytest.raises(BrowserError, match="POST requests are blocked"):
                await session.click(ref(home.snapshot, 'button "Apply"'))
            assert (await session.view()).title == "Directory"
        assert not any(r.startswith("POST") for r in site.requests)

    async def test_a_redirect_to_a_private_host_is_not_shown(self, site: Site) -> None:
        guard = UrlGuard(fake_dns({"intranet.localhost": "10.0.0.1"}), only_loopback)
        service = BrowserService(BrowserSettings(), guard)
        target = site.url("/secret", host="intranet.localhost")
        try:
            async with service.session() as session:
                with pytest.raises(BrowserError, match="not on the public internet"):
                    await session.open(site.url(f"/redirect?to={target}"))
                assert "SECRET" not in (await session.view()).snapshot
        finally:
            await service.stop()

    async def test_dns_that_changes_after_the_check_is_caught(self, site: Site) -> None:
        # The guard is told the host is public; Chrome still connects to this machine.
        service = BrowserService(
            BrowserSettings(), UrlGuard(fake_dns({"news.localhost": "93.184.216.34"}))
        )
        try:
            async with service.session() as session:
                with pytest.raises(BrowserError, match=r"private address 127\.0\.0\.1"):
                    await session.open(site.url("/secret", host="news.localhost"))
        finally:
            await service.stop()

    @pytest.mark.parametrize(
        "url",
        ["file:///etc/passwd", "chrome://settings", "http://[::1]/", "http://169.254.169.254/"],
    )
    async def test_non_web_and_private_urls_are_refused(self, url: str) -> None:
        assert await UrlGuard().url_problem(url) is not None
