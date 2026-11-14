"""Gig budgets and rate floors, compared as an hourly rate in USD."""

import re

from mozg_outreach.pipelines.freelance.models import BudgetBasis, GigBudget
from mozg_outreach.pipelines.job.models import Money
from mozg_outreach.pipelines.job.salary import USD_PER_UNIT, SalaryParser

# The legacy freelance assumptions: an 8-hour day, 160-hour month, 2000-hour year.
HOURS_PER: dict[BudgetBasis, float] = {"hour": 1, "day": 8, "month": 160, "year": 2000}
DEFAULT_PROJECT_HOURS = 320.0
_PROJECT = re.compile(r"\b(?:fixed|project|total|flat|lump)\b", re.IGNORECASE)
_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*(week|month|day)s?", re.IGNORECASE)
_HOURS_PER_DURATION_UNIT = {"day": 8.0, "week": 40.0, "month": 160.0}


class BudgetParser:
    def __init__(self) -> None:
        self._salary = SalaryParser()

    def parse(self, raw: str | None, duration_raw: str | None) -> GigBudget | None:
        salary = self._salary.parse(raw)
        if raw is None or salary is None or salary.min is None or salary.max is None:
            return None
        basis: BudgetBasis = "project" if _PROJECT.search(raw) else salary.period
        hours = project_hours(duration_raw) if basis == "project" else HOURS_PER[basis]
        rate = USD_PER_UNIT.get(salary.currency) if salary.currency else None
        return GigBudget(
            raw=raw,
            currency=salary.currency,
            min=salary.min,
            max=salary.max,
            basis=basis,
            hourly_min_usd=round(salary.min * rate / hours, 2) if rate else None,
            hourly_max_usd=round(salary.max * rate / hours, 2) if rate else None,
        )


def project_hours(duration_raw: str | None) -> float:
    found = _DURATION.search(duration_raw or "")
    if not found:
        return DEFAULT_PROJECT_HOURS
    return float(found[1]) * _HOURS_PER_DURATION_UNIT[found[2].lower()]


def hourly_usd(floor: Money) -> float | None:
    rate = USD_PER_UNIT.get(floor.currency.upper())
    return round(floor.amount * rate / HOURS_PER[floor.period], 2) if rate else None
