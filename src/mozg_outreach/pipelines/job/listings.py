"""Turn raw scouted listings into deduplicated, normalized `Listing`s."""

import logging
import re
from datetime import date, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel

from mozg_outreach.pipelines.job.models import Listing, ScoutedListing
from mozg_outreach.pipelines.job.salary import SalaryParser

logger = logging.getLogger(__name__)

TRACKING_PARAMS = frozenset({"gclid", "fbclid", "mc_cid", "mc_eid", "ref", "source", "trk"})
_SENIORITY_WORDS = re.compile(r"\b(?:sr|senior|staff|principal|lead|jr|junior)\b")
_ROLE_SUFFIX = re.compile(r"\b(?:engineer|developer|programmer)\b")
_LEGAL = re.compile(r"\b(?:inc|ltd|gmbh|llc|sa|srl|ag|bv|oy|ab|plc|corp)\b")
_AGO = re.compile(r"(\d+)\+?\s*(hour|day|week|month)s?\s+ago", re.IGNORECASE)
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DAYS_PER_UNIT = {"hour": 0, "day": 1, "week": 7, "month": 30}


class SourcedListing(BaseModel):
    source: str
    listing: ScoutedListing


class ListingNormalizer:
    def __init__(self, today: date) -> None:
        self._today = today
        self._salary = SalaryParser()

    def normalize(self, scouted: list[SourcedListing]) -> list[Listing]:
        """URL dedup first, then the same posting seen on different hosts, oldest kept."""
        by_url: dict[str, Listing] = {}
        for item in scouted:
            listing = self._listing(item)
            current = by_url.get(listing.key)
            by_url[listing.key] = _merge(current, listing) if current else listing
        by_print: dict[tuple[str, str, str], Listing] = {}
        for listing in by_url.values():
            key = fingerprint(listing)
            current = by_print.get(key)
            by_print[key] = _merge_posting(current, listing) if current else listing
        logger.info(
            "normalized %d scouted listings to %d (%d after url dedup)",
            len(scouted),
            len(by_print),
            len(by_url),
        )
        return list(by_print.values())

    def posted_on(self, raw: str | None) -> date | None:
        if not raw:
            return None
        text = raw.strip().lower()
        if text in {"today", "just now", "new"}:
            return self._today
        if text == "yesterday":
            return self._today - timedelta(days=1)
        iso = _ISO.search(text)
        if iso:
            try:
                return date(int(iso[1]), int(iso[2]), int(iso[3]))
            except ValueError:
                return None
        ago = _AGO.search(text)
        if ago:
            return self._today - timedelta(days=int(ago[1]) * _DAYS_PER_UNIT[ago[2].lower()])
        return None

    def _listing(self, item: SourcedListing) -> Listing:
        raw = item.listing
        return Listing(
            key=canonical_url(raw.url),
            title=raw.title.strip(),
            company=raw.company.strip(),
            location_string=raw.location_string,
            remote_status_raw=raw.remote_status_raw,
            url=raw.url,
            sources=[item.source],
            posted_on=self.posted_on(raw.posted_date),
            salary=self._salary.parse(raw.salary_string),
            summary=raw.summary_snippet,
            apply_url=raw.apply_url,
        )


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def fingerprint(listing: Listing) -> tuple[str, str, str]:
    """Without a company, two "Python developer, remote" posts say nothing about being one job."""
    title = _ROLE_SUFFIX.sub(" ", _SENIORITY_WORDS.sub(" ", _plain(listing.title)))
    company = _LEGAL.sub(" ", _plain(listing.company))
    if not company.strip():
        return listing.key, "", ""
    return " ".join(title.split()), " ".join(company.split()), _place(listing)


def _plain(text: str) -> str:
    return re.sub(r"[^\w\s]", " ", text.lower())


def _place(listing: Listing) -> str:
    remote = f"{listing.remote_status_raw or ''} {listing.location_string or ''}".lower()
    if "remote" in remote:
        return "remote"
    location = (listing.location_string or "").split(",")[-1].strip().lower()
    return location or "unknown"


def _merge(kept: Listing, other: Listing) -> Listing:
    """Same URL from two sources: keep the fuller record, remember both sources."""
    fuller = max((kept, other), key=_filled)
    sources = list(dict.fromkeys([*kept.sources, *other.sources]))
    return fuller.model_copy(update={"sources": sources})


def _merge_posting(kept: Listing, other: Listing) -> Listing:
    """Same posting at different URLs: the earliest one is the original."""
    first, second = sorted((kept, other), key=lambda item: item.posted_on or date.max)
    return first.model_copy(
        update={
            "sources": list(dict.fromkeys([*first.sources, *second.sources])),
            "salary": first.salary or second.salary,
            "merged_urls": [*first.merged_urls, second.url, *second.merged_urls],
        }
    )


def _filled(listing: Listing) -> int:
    return sum(v is not None and v != "" for v in listing.model_dump().values())
