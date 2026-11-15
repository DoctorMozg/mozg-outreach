"""Decide from listing text whether the user may take a job from where they live."""

import re
from typing import Literal

from pydantic import BaseModel

from mozg_outreach.pipelines.seeker.models import CvProfile, Listing, PlacePreferences


def _codes(text: str) -> frozenset[str]:
    return frozenset(text.split())


EU = _codes("AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE")
EUROPE = EU | _codes("GB CH NO IS LI UA RS BA AL MK ME MD")
LATAM = _codes("MX BR AR CO CL PE UY CR")
REGIONS: dict[str, frozenset[str]] = {
    "eu": EU,
    "european union": EU,
    "eea": EU | _codes("IS LI NO"),
    "europe": EUROPE,
    "emea": EUROPE | _codes("AE IL SA TR EG ZA NG KE MA"),
    "uk": _codes("GB"),
    "united kingdom": _codes("GB"),
    "us": _codes("US"),
    "usa": _codes("US"),
    "united states": _codes("US"),
    "north america": _codes("US CA"),
    "latam": LATAM,
    "latin america": LATAM,
    "apac": _codes("AU NZ IN SG JP KR PH ID MY TH VN"),
}
COUNTRIES: dict[str, str] = {
    "austria": "AT", "belgium": "BE", "bulgaria": "BG", "croatia": "HR", "cyprus": "CY",
    "czechia": "CZ", "czech republic": "CZ", "denmark": "DK", "estonia": "EE", "finland": "FI",
    "france": "FR", "germany": "DE", "deutschland": "DE", "greece": "GR", "hungary": "HU",
    "ireland": "IE", "italy": "IT", "latvia": "LV", "lithuania": "LT", "luxembourg": "LU",
    "malta": "MT", "netherlands": "NL", "poland": "PL", "portugal": "PT", "romania": "RO",
    "slovakia": "SK", "slovenia": "SI", "spain": "ES", "sweden": "SE", "norway": "NO",
    "switzerland": "CH", "iceland": "IS", "ukraine": "UA", "serbia": "RS", "england": "GB",
    "scotland": "GB", "canada": "CA", "mexico": "MX", "brazil": "BR", "argentina": "AR",
    "india": "IN", "singapore": "SG", "australia": "AU", "new zealand": "NZ", "japan": "JP",
    "israel": "IL", "turkey": "TR", "united arab emirates": "AE", "uae": "AE",
}  # fmt: skip
ANYWHERE = re.compile(r"\b(?:anywhere|worldwide|world-wide|global(?:ly)?|any location)\b", re.I)
REMOTE = re.compile(r"\bremote\b", re.I)
ON_SITE = re.compile(r"\b(?:on-?site|in[- ]office|hybrid)\b", re.I)
REQUIRED_PLACE = re.compile(
    r"\b(?:must be (?:located|based|resident) in|eligible to work in|authori[sz]ed to work in)"
    r"\s+(?:the\s+)?([^.,;()\n]{2,40})",
    re.I,
)
# Abbreviations match only in capitals, so the word "us" isn't read as the country.
_ABBREVIATIONS = frozenset({"eu", "eea", "uk", "us", "usa", "uae", "emea", "latam", "apac"})
_REGION_NAMES = sorted({*REGIONS, *COUNTRIES} - _ABBREVIATIONS, key=len, reverse=True)
_PLACE = re.compile(
    r"\b(?:EU|EEA|UK|US|USA|UAE|EMEA|LATAM|APAC)\b|(?i:\b(?:"
    + "|".join(re.escape(n) for n in _REGION_NAMES)
    + r")\b)"
)

Verdict = Literal["yes", "no", "unknown"]


class Eligibility(BaseModel):
    verdict: Verdict
    evidence: str


class EligibilityChecker:
    def __init__(self, cv: CvProfile, preferences: PlacePreferences, blockers: list[str]) -> None:
        self._country = cv.country_code.upper()
        self._city = cv.current_location.split(",")[0].strip().lower()
        self._preferences = preferences
        self._blockers = [b for b in blockers if b.strip()]
        self._allowed = frozenset(
            code for name in preferences.regions_allowed for code in places_in(name)
        ) | {self._country}

    def check(self, listing: Listing) -> Eligibility:
        text = " ".join(
            part
            for part in (listing.remote_status_raw, listing.location_string, listing.summary)
            if part
        )
        for blocker in self._blockers:
            if blocker.lower() in text.lower():
                return Eligibility(verdict="no", evidence=blocker)
        required = REQUIRED_PLACE.search(text)
        if required:
            places = places_in(required[1])
            if places:
                verdict: Verdict = "yes" if self._country in places else "no"
                return Eligibility(verdict=verdict, evidence=required[0])
        if REMOTE.search(text):
            return self._remote(listing)
        return self._on_site(listing, text)

    def _remote(self, listing: Listing) -> Eligibility:
        # The location fields are the board's structured data; prose only fills their gaps.
        for field in (listing.location_string, listing.remote_status_raw):
            found = self._scope(field or "")
            if found:
                return found
        if ANYWHERE.search(listing.summary):
            return Eligibility(verdict="yes", evidence=_snippet(listing.summary, ANYWHERE))
        return self._scope(listing.summary) or Eligibility(
            verdict="unknown", evidence="remote, scope not stated"
        )

    def _scope(self, text: str) -> Eligibility | None:
        """Read the region named right after each "remote" in `text`."""
        for match in REMOTE.finditer(text):
            window = text[match.start() : match.end() + 60]
            if ANYWHERE.search(window):
                return Eligibility(verdict="yes", evidence=_snippet(window, ANYWHERE))
            places = places_in(window)
            if places:
                verdict: Verdict = "yes" if self._country in places else "no"
                return Eligibility(verdict=verdict, evidence=_snippet(window, _PLACE))
        return None

    def _on_site(self, listing: Listing, text: str) -> Eligibility:
        location = listing.location_string or ""
        if self._preferences.remote == "required" and ON_SITE.search(text):
            return Eligibility(verdict="no", evidence=_snippet(text, ON_SITE))
        if self._city and self._city in location.lower():
            return Eligibility(verdict="yes", evidence=location)
        places = places_in(location)
        if not places:
            return Eligibility(verdict="unknown", evidence=location or "no location given")
        if self._preferences.remote == "required":
            # Without the word "remote" a snippet may simply have left it out.
            return Eligibility(verdict="unknown", evidence=location)
        verdict: Verdict = "yes" if places & self._allowed else "no"
        return Eligibility(verdict=verdict, evidence=location)


def places_in(text: str) -> frozenset[str]:
    """Country codes named in `text`, by country or region name."""
    codes: set[str] = set()
    for match in _PLACE.finditer(text):
        name = match[0].lower()
        if name in REGIONS:
            codes |= REGIONS[name]
        elif name in COUNTRIES:
            codes.add(COUNTRIES[name])
    return frozenset(codes)


def _snippet(text: str, pattern: re.Pattern[str]) -> str:
    found = pattern.search(text)
    if not found:
        return text[:80]
    return text[max(0, found.start() - 30) : found.end() + 30].strip()
