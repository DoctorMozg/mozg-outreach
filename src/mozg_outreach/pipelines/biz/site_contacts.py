"""Reads a company's own contact, team and privacy pages for published emails and profile links."""

import asyncio
import logging
import re
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from mozg_outreach.pipelines.biz.enrichment_models import EmailContact, ProfileLink, SiteContacts

logger = logging.getLogger(__name__)

MAX_PAGES = 8
MAX_PAGE_BYTES = 2_000_000
# Pages worth reading, best first: where companies put addresses and name their people.
_PAGE_KINDS = (
    re.compile(r"contact|get-in-touch|reach-us|enquir", re.I),
    re.compile(r"privacy|data-protection|gdpr|pdpl", re.I),
    re.compile(r"about|team|leadership|management|people|board|founder|doctors", re.I),
    re.compile(r"media|press|newsroom", re.I),
    re.compile(r"legal|imprint|impressum|terms", re.I),
    re.compile(r"careers|jobs", re.I),
)
_FALLBACK_PATHS = ("/contact", "/contact-us", "/about", "/about-us", "/privacy-policy")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_AT = re.compile(r"\s*[\[(\{]\s*at\s*[\])\}]\s*", re.I)
_DOT = re.compile(r"\s*[\[(\{]\s*dot\s*[\])\}]\s*", re.I)
_PROFILE = re.compile(r"linkedin\.com/in/([^/?#\s\"']+)", re.I)
_NOT_EMAIL = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")
_GENERIC_LABELS = frozenset({"com", "co", "org", "net", "gov", "ac", "edu", "mail"})
_MIN_BRAND = 4
_JUNK_DOMAINS = frozenset({"example.com", "domain.com", "email.com", "sentry.io", "wixpress.com"})


def profile_url(href: str) -> str | None:
    """One canonical form for a LinkedIn person link, whatever country host or tracking it has."""
    match = _PROFILE.search(href)
    return f"https://www.linkedin.com/in/{match.group(1).rstrip('/')}" if match else None


class SiteReader(Protocol):
    async def read(self, domain: str) -> SiteContacts: ...


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.text: list[str] = []
        self.protected: list[str] = []
        self._href: str | None = None
        self._label: list[str] = []
        self._skip = 0
        self._forms = 0
        # A form with a free-text box is a contact form; a newsletter signup has none.
        self.has_contact_form = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        if tag == "form":
            self._forms += 1
        if tag == "textarea" and self._forms:
            self.has_contact_form = True
        if (cf := values.get("data-cfemail")) is not None:
            self.protected.append(cf)
        if tag == "a" and (href := values.get("href")):
            self._href, self._label = href, []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        if tag == "form" and self._forms:
            self._forms -= 1
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._label).split())))
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self.text.append(data)
        if self._href is not None:
            self._label.append(data)


class SiteSweep:
    """Fetches the homepage and its likeliest contact pages; never submits or logs in."""

    def __init__(self, client: httpx.AsyncClient, max_pages: int = MAX_PAGES) -> None:
        self._client = client
        self._max_pages = max_pages

    async def read(self, domain: str) -> SiteContacts:
        home = f"https://{domain}/"
        found = SiteContacts()
        page = await self._fetch(home)
        if page is None:
            found.concerns.append(f"Couldn't read {home} to look for published contacts.")
            return found
        url, parsed = page
        pages = [(url, parsed)]
        targets = self._targets(url, parsed)
        fetched = await asyncio.gather(*(self._fetch(t) for t in targets))
        pages += [p for p in fetched if p is not None]
        for page_url, page_parsed in pages:
            # Redirects land several links on one page; read it once.
            if page_url not in found.pages:
                _collect(found, page_url, page_parsed, domain)
        # Contact pages come right after the homepage, so prefer theirs over a homepage form.
        with_form = [u for u, p in [*pages[1:], pages[0]] if p.has_contact_form]
        found.contact_form = with_form[0] if with_form else None
        logger.info(
            "site sweep of %s: %d pages, %d emails, %d profiles",
            domain,
            len(found.pages),
            len(found.emails),
            len(found.profiles),
        )
        return found

    def _targets(self, home: str, page: _Page) -> list[str]:
        host = _bare(urlsplit(home).hostname)
        ranked: list[tuple[int, str]] = []
        for href, label in page.links:
            url = urljoin(home, href).split("#")[0]
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or _bare(parts.hostname) != host:
                continue
            for rank, kind in enumerate(_PAGE_KINDS):
                if kind.search(parts.path) or kind.search(label):
                    ranked.append((rank, url))
                    break
        ranked += [(len(_PAGE_KINDS), urljoin(home, p)) for p in _FALLBACK_PATHS]
        seen = {home.rstrip("/")}
        targets = []
        for _, url in sorted(ranked, key=lambda r: r[0]):
            if url.rstrip("/") not in seen:
                seen.add(url.rstrip("/"))
                targets.append(url)
        return targets[: self._max_pages]

    async def _fetch(self, url: str) -> tuple[str, _Page] | None:
        """A missing or unreadable page is normal here: most fallback paths don't exist."""
        try:
            response = await self._client.get(url)
        except httpx.HTTPError as exc:
            logger.debug("site sweep could not fetch %s: %s", url, exc)
            return None
        kind = response.headers.get("content-type", "")
        if response.status_code != 200 or "html" not in kind:
            logger.debug("site sweep skipped %s: %d %s", url, response.status_code, kind)
            return None
        parsed = _Page()
        parsed.feed(response.text[:MAX_PAGE_BYTES])
        return str(response.url), parsed


def _collect(found: SiteContacts, url: str, page: _Page, domain: str) -> None:
    found.pages.append(url)
    known = {e.address for e in found.emails}

    def add(address: str, anywhere: bool) -> None:
        address = address.strip().strip(".").lower()
        if address in known or not _plausible(address):
            return
        # A mailto is the site's own choice; an address in text must be on the company's domain.
        if anywhere or _related(address.split("@")[1], domain):
            known.add(address)
            found.emails.append(EmailContact(address=address, kind="published", source_url=url))

    for href, label in page.links:
        if href.lower().startswith("mailto:"):
            add(unquote(href[7:]).split("?")[0], anywhere=True)
        elif (profile := profile_url(href)) and all(p.url != profile for p in found.profiles):
            found.profiles.append(ProfileLink(url=profile, text=label, source_url=url))
    for encoded in page.protected:
        if decoded := _cloudflare(encoded):
            add(decoded, anywhere=True)
    text = _DOT.sub(".", _AT.sub("@", " ".join(page.text)))
    for address in _EMAIL.findall(text):
        add(address, anywhere=False)


def _cloudflare(encoded: str) -> str | None:
    """Cloudflare hides addresses as hex XOR-ed with the first byte; browsers decode it."""
    try:
        raw = bytes.fromhex(encoded)
    except ValueError:
        return None
    if len(raw) < 2:
        return None
    return bytes(b ^ raw[0] for b in raw[1:]).decode("utf-8", errors="replace")


def _plausible(address: str) -> bool:
    if not _EMAIL.fullmatch(address) or address.endswith(_NOT_EMAIL):
        return False
    return address.split("@")[1] not in _JUNK_DOMAINS


def _bare(host: str | None) -> str:
    return (host or "").lower().removeprefix("www.")


def _related(email_domain: str, domain: str) -> bool:
    """The company's domain, or its group's: alfuttaim.com for healthhubalfuttaim.com."""
    site = _bare(domain)
    if email_domain == site or email_domain.endswith(f".{site}"):
        return True
    names = [n for n in email_domain.split(".")[:-1] if n not in _GENERIC_LABELS]
    return bool(names) and len(names[-1]) >= _MIN_BRAND and names[-1] in site.split(".")[0]
