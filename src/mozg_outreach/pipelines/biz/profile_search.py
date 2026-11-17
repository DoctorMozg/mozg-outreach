"""Finds a person's LinkedIn profile through a search API; LinkedIn itself is never fetched."""

import asyncio
import html
import logging
import re
import time
import unicodedata
from typing import Protocol

import httpx
from pydantic import BaseModel, SecretStr

from mozg_outreach.pipelines.biz.site_contacts import profile_url

logger = logging.getLogger(__name__)

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
# The free Brave plan allows one query a second.
MIN_INTERVAL = 1.1
RESULTS = 10
MIN_FIRM_WORD = 4
_HONORIFICS = frozenset({"dr", "mr", "mrs", "ms", "miss", "prof", "eng", "sir", "dame"})
# Words in company names that also appear in unrelated profiles, professions included.
_GENERIC = frozenset(
    {"the", "and", "group", "company", "holding", "holdings", "international", "global"}
    | {"llc", "llp", "ltd", "inc", "plc", "fz", "fze", "fzco", "dmcc", "pjsc", "co"}
    | {"partners", "legal", "law", "consultants", "consulting", "services", "solutions"}
    | {"chartered", "accountants", "accountant", "accounting", "audit", "auditors", "auditing"}
    | {"tax", "advisory", "engineering", "engineers", "technologies", "technology", "tech"}
    | {"software", "digital", "systems", "medical", "clinic", "clinics", "healthcare"}
    | {"trading", "contracting", "properties", "real", "estate", "investments", "capital"}
    | {"management", "marketing", "media", "design", "studio", "labs", "agency"}
    | {"advocates", "lawyers", "solicitors", "attorneys", "firm", "office", "centre", "center"}
    # Places name half the region's employers.
    | {"dubai", "abu", "dhabi", "sharjah", "ajman", "emirates", "uae", "gulf", "middle", "east"}
    | {"saudi", "arabia", "riyadh", "jeddah", "doha", "qatar", "bahrain", "kuwait", "oman"}
)
_TAG = re.compile(r"<[^>]+>")
# LinkedIn titles read "Name - Role - Employer | LinkedIn".
_TITLE_PARTS = re.compile(r"\s+[-\u2013\u2014\u00b7|]\s+")


class ProfileSearchError(Exception):
    """The search provider refused or failed; the lookup can be retried on the next enrichment."""


class ProfileHit(BaseModel):
    url: str
    title: str
    snippet: str


class ProfileFinder(Protocol):
    async def find(self, name: str, company: str) -> ProfileHit | None: ...


class BraveProfiles:
    """Runs `site:linkedin.com/in "<name>" <company>` on Brave Search and checks the results."""

    def __init__(self, client: httpx.AsyncClient, api_key: SecretStr) -> None:
        self._client = client
        self._key = api_key
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def find(self, name: str, company: str) -> ProfileHit | None:
        if len(_first_and_last(name)) < 2:
            logger.info("no linkedin search for %r: a single name matches too many people", name)
            return None
        person = " ".join(t for t in name.split() if _plain(t).strip(".") not in _HONORIFICS)
        query = f'site:linkedin.com/in "{person}" {company}'
        results = await self._search(query)
        hit = best_profile(results, name, company)
        logger.info("linkedin search for %s at %s: %s", name, company, hit.url if hit else "none")
        return hit

    async def _search(self, query: str) -> list[ProfileHit]:
        async with self._lock:
            wait = self._last + MIN_INTERVAL - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                response = await self._client.get(
                    BRAVE_URL,
                    params={"q": query, "count": RESULTS},
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": self._key.get_secret_value(),
                    },
                )
            except httpx.HTTPError as exc:
                raise ProfileSearchError(f"Brave Search failed: {exc}") from exc
            finally:
                self._last = time.monotonic()
        if response.status_code != httpx.codes.OK:
            raise ProfileSearchError(f"Brave Search answered {response.status_code}")
        results = response.json().get("web", {}).get("results", [])
        return [
            ProfileHit(
                url=r.get("url", ""),
                title=_text(r.get("title", "")),
                snippet=_text(r.get("description", "")),
            )
            for r in results
        ]


def best_profile(results: list[ProfileHit], name: str, company: str) -> ProfileHit | None:
    """The first profile whose result names the person and the company as current employer."""
    # "Al" in Al-Futtaim would match half the region's surnames, the person's own included.
    if len(_first_and_last(name)) < 2:
        return None
    own = _tokens(name)
    firm = {t for t in _tokens(company) - _GENERIC - own if len(t) >= MIN_FIRM_WORD}
    firm = firm or _tokens(company) - own
    for result in results:
        url = profile_url(result.url)
        if url is None or not names_person(name, result.title):
            continue
        employer = _employer(result.title)
        if employer is not None and not firm & _tokens(employer):
            continue  # a namesake, or someone who has moved on
        if firm & ((_tokens(result.title) | _tokens(result.snippet)) - own):
            return result.model_copy(update={"url": url})
    return None


def _employer(title: str) -> str | None:
    """The employer slot of a "Name - Role - Employer | LinkedIn" title, when it has one."""
    parts = [p for p in _TITLE_PARTS.split(title) if p.strip().lower() != "linkedin"]
    return parts[-1] if len(parts) >= 3 else None


def names_person(name: str, text: str) -> bool:
    """The text carries the person's first and last name, honorifics and accents aside."""
    first_last = _first_and_last(name)
    return bool(first_last) and first_last <= _tokens(text)


def _first_and_last(name: str) -> set[str]:
    words = [w for w in re.findall(r"[a-z0-9]+", _plain(name)) if w not in _HONORIFICS]
    return {words[0], words[-1]} if words else set()


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", _plain(text)) if len(t) > 1}


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text)
    return "".join(c for c in folded if not unicodedata.combining(c)).lower()


def _text(markup: str) -> str:
    return html.unescape(_TAG.sub("", markup)).strip()
