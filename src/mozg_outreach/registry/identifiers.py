"""Normalized identifiers: two sightings with the same identifier are the same person or company."""

import re

from pydantic import BaseModel

from mozg_outreach.domain import IdentifierKind

_DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
_EMAIL = re.compile(r"^[a-z0-9._%+-]+@([a-z0-9-]+(\.[a-z0-9-]+)+)$")
_LINKEDIN = re.compile(r"^(?:[a-z]{2,3}\.)?linkedin\.com/(in|company)/([^/?#]+)")
# Trailing only, so "sp z o o" goes but a middle initial "O" in a person's name stays.
_LEGAL_SUFFIXES = frozenset(
    {"ou", "o\u00fc", "as", "ab", "oy", "gmbh", "ag", "ltd", "limited", "inc", "llc", "plc"}
    | {"sa", "bv", "srl", "corp", "co", "sp", "z", "o"}
)
_TELEGRAM = re.compile(r"^(?:@|t\.me/|telegram\.me/)?([a-z][a-z0-9_]{4,31})$")


class Identifier(BaseModel, frozen=True):
    kind: IdentifierKind
    value: str


def normalize_domain(raw: str | None) -> str | None:
    if not raw:
        return None
    host = _strip_scheme(raw).split("/", 1)[0].split(":", 1)[0]
    return host if _DOMAIN.match(host) else None


def normalize(kind: IdentifierKind, raw: str | None) -> Identifier | None:
    """Canonical form of a raw identifier, or None when it isn't a valid one of that kind."""
    if not raw or not raw.strip():
        return None
    value: str | None
    match kind:
        case IdentifierKind.DOMAIN:
            value = normalize_domain(raw)
        case IdentifierKind.EMAIL:
            email = raw.strip().lower().removeprefix("mailto:")
            value = email if _EMAIL.match(email) else None
        case IdentifierKind.LINKEDIN:
            found = _LINKEDIN.match(_strip_scheme(raw))
            value = f"linkedin.com/{found[1]}/{found[2]}" if found else None
        case IdentifierKind.TELEGRAM:
            found = _TELEGRAM.match(_strip_scheme(raw).rstrip("/"))
            value = found[1] if found else None
        case IdentifierKind.NAME_AT_COMPANY:
            name, _, employer = raw.rpartition("@")
            value = f"{name_key(name)}@{employer.strip().lower()}" if name_key(name) else None
    return Identifier(kind=kind, value=value) if value else None


def name_key(name: str) -> str:
    """Comparable form of a name: casefolded, no punctuation, no legal suffix or former name."""
    text = re.sub(r"\(.*?\)", " ", name.casefold())
    text = re.sub(r"[^\w\s]", " ", text)
    words = text.split()
    while len(words) > 1 and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def person_at_company(name: str, employer: str) -> Identifier | None:
    return normalize(IdentifierKind.NAME_AT_COMPANY, f"{name}@{employer}")


def _strip_scheme(raw: str) -> str:
    return re.sub(r"^[a-z]+://", "", raw.strip().lower()).removeprefix("www.")
