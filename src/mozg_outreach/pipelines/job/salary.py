"""Parse salary strings and compare them in USD per year."""

import re

from mozg_outreach.pipelines.job.models import Money, Period, Salary

# Reference rates (USD per unit) from the legacy pipeline; good to about 5%, which is
# enough to compare a band with a floor. Unknown currencies stay unconverted.
USD_PER_UNIT: dict[str, float] = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "CHF": 1.13,
    "CAD": 0.74,
    "AUD": 0.66,
    "SEK": 0.095,
    "NOK": 0.092,
    "DKK": 0.145,
    "PLN": 0.25,
    "INR": 0.012,
    "JPY": 0.0066,
}
ANNUAL_FACTOR: dict[Period, int] = {"year": 1, "month": 12, "day": 240, "hour": 2080}

_SYMBOLS = {"$": "USD", "\u20ac": "EUR", "\u00a3": "GBP", "\u20b9": "INR", "\u00a5": "JPY"}
_CODES = re.compile(r"\b(" + "|".join(USD_PER_UNIT) + r"|[A-Z]{3}(?=\s*\d))\b")
_AMOUNT = re.compile(r"(\d{1,3}(?:[,.\u00a0 ]\d{3})+|\d+(?:[.,]\d+)?)\s*([kK])?(?![\w])")
# Only these between two amounts make a range; "45/hr, 2000 hours" is not one.
_RANGE = re.compile(
    r"\s*(?:[-\u2013\u2014]|to|bis|\u0434\u043e)\s*[$\u20ac\u00a3]?\s*", re.IGNORECASE
)
_PERIODS: list[tuple[Period, re.Pattern[str]]] = [
    ("hour", re.compile(r"/\s*h(?:ou)?r\b|per hour|hourly|\bph\b", re.IGNORECASE)),
    ("day", re.compile(r"/\s*day\b|per day|daily", re.IGNORECASE)),
    ("month", re.compile(r"/\s*mo(?:nth)?\b|per month|monthly|\bpm\b", re.IGNORECASE)),
]


class SalaryParser:
    def parse(self, raw: str | None) -> Salary | None:
        """None when the text holds no amount; an unknown currency keeps USD fields empty."""
        if not raw or not raw.strip():
            return None
        found = list(_AMOUNT.finditer(raw))
        if not found:
            return None
        low = high = _amount(*found[0].groups())
        if len(found) > 1 and _RANGE.fullmatch(raw[found[0].end() : found[1].start()]):
            high = _amount(*found[1].groups())
        if low < 1000 <= high and low * 1000 <= high * 2:
            # "60-80k": the k written once applies to both ends of the range.
            low *= 1000
        low, high = min(low, high), max(low, high)
        currency = _currency(raw)
        period: Period = next((p for p, rx in _PERIODS if rx.search(raw)), "year")
        rate = USD_PER_UNIT.get(currency) if currency else None
        factor = ANNUAL_FACTOR[period]
        return Salary(
            raw=raw,
            currency=currency,
            min=low,
            max=high,
            period=period,
            annual_min_usd=round(low * factor * rate) if rate else None,
            annual_max_usd=round(high * factor * rate) if rate else None,
        )


def annual_usd(money: Money) -> float | None:
    rate = USD_PER_UNIT.get(money.currency.upper())
    return money.amount * ANNUAL_FACTOR[money.period] * rate if rate else None


def _currency(raw: str) -> str | None:
    code = _CODES.search(raw.upper())
    if code:
        return code.group(1)
    return next((c for symbol, c in _SYMBOLS.items() if symbol in raw), None)


def _amount(number: str, thousands: str | None) -> float:
    grouped = re.fullmatch(r"\d{1,3}(?:[,.\u00a0 ]\d{3})+", number)
    value = float(re.sub(r"\D", "", number)) if grouped else float(number.replace(",", "."))
    return value * 1000 if thousands else value
