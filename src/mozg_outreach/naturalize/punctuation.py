"""Plain-ASCII punctuation check for drafts; a superset of the legacy forbidden-punctuation.sh."""

import re
import unicodedata
from collections.abc import Iterable

from pydantic import BaseModel

from mozg_outreach.naturalize.models import SoftPunctuation

HARD_BANS = {
    "em_dash": re.compile("\u2014"),
    "en_dash": re.compile("\u2013"),
    "curly_quote": re.compile("[\u2018\u2019\u201c\u201d]"),
    "ellipsis": re.compile("\u2026"),
    "arrow": re.compile("[\u2190\u2192\u21d2]"),
}
SOFT_BANS: dict[SoftPunctuation, re.Pattern[str]] = {
    "semicolon": re.compile(";"),
    # A spaced hyphen between words is a dash in disguise; "50 - 300" is a range and stays.
    "spaced_hyphen": re.compile(r"(?<=[^\s\d]) - (?=[^\s\d])"),
    "double_hyphen": re.compile("--"),
    "three_dots": re.compile(r"\.{3,}"),
}
_EXEMPT = re.compile(r"https?://\S+|[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_NAMED = frozenset(c for rx in HARD_BANS.values() for c in rx.pattern.strip("[]"))
_CURLY = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u00a0": " "})


class PunctuationIssue(BaseModel):
    name: str
    line: int
    char: str


class PunctuationChecker:
    def __init__(
        self, allowed: Iterable[SoftPunctuation] = (), preserve: Iterable[str] = ()
    ) -> None:
        lifted = set(allowed)
        soft: dict[str, re.Pattern[str]] = {
            name: rx for name, rx in SOFT_BANS.items() if name not in lifted
        }
        self._patterns: dict[str, re.Pattern[str]] = {**HARD_BANS, **soft}
        # Longest first, so a term containing a shorter one is removed whole.
        self._preserve = sorted((t for t in preserve if t), key=len, reverse=True)

    def check(self, text: str) -> list[PunctuationIssue]:
        """Report each banned kind once per line, with 1-based line numbers."""
        issues: list[PunctuationIssue] = []
        for number, raw in enumerate(text.splitlines(), start=1):
            line = self._strip_exempt(raw)
            for name, rx in self._patterns.items():
                found = rx.search(line)
                if found:
                    issues.append(PunctuationIssue(name=name, line=number, char=found.group()))
            other = _other_non_ascii(line)
            if other:
                issues.append(PunctuationIssue(name="non_ascii", line=number, char=other))
        return issues

    def _strip_exempt(self, line: str) -> str:
        for term in self._preserve:
            line = line.replace(term, " ")
        return _EXEMPT.sub(" ", line)


def straighten_quotes(text: str) -> str:
    """Curly quotes and non-breaking spaces have exact ASCII twins, so fixing them is lossless."""
    return text.translate(_CURLY)


def _other_non_ascii(line: str) -> str | None:
    for char in line:
        if ord(char) < 128 or char in _NAMED:
            continue
        category = unicodedata.category(char)
        # Letters (accented names) and currency symbols are fine in prose.
        if category[0] in "PZ" or (category[0] == "S" and category != "Sc"):
            return char
    return None
