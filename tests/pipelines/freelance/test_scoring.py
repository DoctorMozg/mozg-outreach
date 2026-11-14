from datetime import date

import pytest

from mozg_outreach.pipelines.freelance.budget import BudgetParser, hourly_usd, project_hours
from mozg_outreach.pipelines.freelance.models import FreelanceStrategy, GigBudget
from mozg_outreach.pipelines.job.models import Money
from mozg_outreach.pipelines.job.salary import USD_PER_UNIT
from mozg_outreach.pipelines.job.skills import SkillMatcher
from mozg_outreach.scoring.freelance import FreelanceScorer, GigScoreInput

from .conftest import SCOPE, gig, gig_judgment

EUR = USD_PER_UNIT["EUR"]


class TestBudget:
    @pytest.mark.parametrize(
        ("raw", "duration", "basis", "hourly"),
        [
            ("EUR 800/day", None, "day", 100 * EUR),
            ("$60-80/hr", None, "hour", 60.0),
            ("$6,400 fixed price", "4 weeks", "project", 40.0),
            ("$9,600 project", None, "project", 30.0),
        ],
    )
    def test_hourly_equivalent(
        self, raw: str, duration: str | None, basis: str, hourly: float
    ) -> None:
        budget = BudgetParser().parse(raw, duration)
        assert budget is not None
        assert budget.basis == basis
        assert budget.hourly_min_usd == pytest.approx(hourly, abs=0.01)

    def test_no_amount_is_none(self) -> None:
        assert BudgetParser().parse("Negotiable", None) is None

    @pytest.mark.parametrize(("raw", "hours"), [("3 months", 480), ("2 weeks", 80), (None, 320)])
    def test_project_hours(self, raw: str | None, hours: float) -> None:
        assert project_hours(raw) == hours

    def test_floor_per_period(self) -> None:
        assert hourly_usd(Money(amount=160, currency="USD", period="day")) == 20.0


def budget(hourly_low: float, hourly_high: float) -> GigBudget:
    return GigBudget(
        raw="x", currency="USD", min=1, max=1, basis="hour",
        hourly_min_usd=hourly_low, hourly_max_usd=hourly_high,
    )  # fmt: skip


class TestScorer:
    def score(self, strategy: FreelanceStrategy, **judged: object) -> GigScoreInput:
        return GigScoreInput(
            gig=gig(budget(100, 120)),
            skills=SkillMatcher(strategy).match(SCOPE),
            judgment=gig_judgment(**judged),
            strategy=strategy,
            today=date(2026, 11, 14),
        )

    def test_budget_band_uses_hourly_floor(self, strategy: FreelanceStrategy) -> None:
        floor = 80 * EUR
        value = FreelanceScorer.budget_fit(gig(budget(floor * 0.9, floor * 1.2)), strategy)
        assert value.value == 100.0
        assert FreelanceScorer.budget_fit(gig(None), strategy).value == 50.0

    def test_no_floor_leaves_budget_unassessed(self, strategy: FreelanceStrategy) -> None:
        prefs = strategy.preferences.model_copy(update={"rate_floor": None})
        unset = strategy.model_copy(update={"preferences": prefs})
        assert FreelanceScorer.budget_fit(gig(budget(10, 20)), unset).value is None

    @pytest.mark.parametrize(
        ("discipline", "points"), [("engineering", 100.0), ("data", 70.0), ("design", 20.0)]
    )
    def test_discipline_fit(
        self, strategy: FreelanceStrategy, discipline: str, points: float
    ) -> None:
        judged = gig_judgment(discipline=discipline)
        assert FreelanceScorer.discipline_fit(judged, strategy).value == points

    def test_unwanted_engagement_type_caps_total(self, strategy: FreelanceStrategy) -> None:
        score = FreelanceScorer().score(self.score(strategy, engagement_type="fractional_retainer"))
        assert score.total == 40.0
        assert score.blockers == ["engagement type fractional_retainer is not one the user wants"]

    def test_clean_gig_scores_high(self, strategy: FreelanceStrategy) -> None:
        score = FreelanceScorer().score(self.score(strategy))
        assert score.blockers == []
        assert score.total is not None
        assert score.total >= 80
        assert {a.axis for a in score.axes} == set(strategy.scoring_weights.model_dump())
