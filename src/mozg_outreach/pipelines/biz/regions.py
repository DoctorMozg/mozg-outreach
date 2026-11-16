"""Place words for the regions whose rules change what the pipelines do."""

import re

UAE_PLACES = (
    "united arab emirates",
    "uae",
    "emirates",
    "dubai",
    "abu dhabi",
    "sharjah",
    "ajman",
    "ras al khaimah",
    "fujairah",
)
GCC_PLACES = (
    *UAE_PLACES,
    "saudi arabia",
    "ksa",
    "riyadh",
    "jeddah",
    "dammam",
    "qatar",
    "doha",
    "kuwait",
    "bahrain",
    "manama",
    "oman",
    "muscat",
)


def mentions(text: str, words: tuple[str, ...]) -> bool:
    folded = text.casefold()
    return any(re.search(rf"\b{re.escape(w)}\b", folded) for w in words)
