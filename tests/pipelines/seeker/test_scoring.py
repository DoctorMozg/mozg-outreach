from datetime import date

import pytest

from mozg_outreach.pipelines.seeker.models import JobStrategy, Money, Salary
from mozg_outreach.pipelines.seeker.salary import SalaryParser
from mozg_outreach.pipelines.seeker.skills import SkillMatcher
from mozg_outreach.scoring.seeker import JobScoreInput, JobScorer

from .conftest import TODAY, judgment, listing


def usd_salary(low: float, high: float) -> Salary:
    salary = SalaryParser().parse(f"${low:,.0f} - ${high:,.0f}")
    assert salary is not None
    return salary


@pytest.fixture
def usd_strategy(strategy: JobStrategy) -> JobStrategy:
    prefs = strategy.preferences.model_copy(
        update={"salary_floor": Money(amount=100000, currency="USD")}
    )
    return strategy.model_copy(update={"preferences": prefs})


class TestAxes:
    @pytest.mark.parametrize(
        ("text", "points"),
        [
            ("Python, PostgreSQL, Go, Kubernetes, Kafka", 95.0),
            ("Python, PostgreSQL, Go", 80.0),
            ("Python, PostgreSQL", 60.0),
        ],
    )
    def test_skill_match_by_nice_to_haves(
        self, strategy: JobStrategy, text: str, points: float
    ) -> None:
        assert JobScorer.skill_match(SkillMatcher(strategy).match(text)).value == points

    @pytest.mark.parametrize(("text", "points"), [("Python, Go, Kafka", 55.0), ("Python", 45.0)])
    def test_partial_must_haves_score_below_complete(
        self, strategy: JobStrategy, text: str, points: float
    ) -> None:
        value = JobScorer.skill_match(SkillMatcher(strategy).match(text))
        assert value.value == points
        assert "missing: PostgreSQL or MySQL" in value.reason

    def test_skill_match_through_synonyms_only_is_weak(self, strategy: JobStrategy) -> None:
        only = strategy.model_copy(update={"must_have_skills": []})
        assert JobScorer.skill_match(SkillMatcher(only).match("Postgres")).value == 40.0

    @pytest.mark.parametrize(
        ("asked", "points"),
        [("senior", 100.0), ("mid", 80.0), ("staff", 70.0), ("principal", 50.0), ("intern", 0.0)],
    )
    def test_seniority_gap(self, asked: str, points: float) -> None:
        value = JobScorer.seniority_fit("senior", judgment(listing_seniority=asked)).value
        assert value == points

    def test_unjudged_axes_are_unassessed(self) -> None:
        assert JobScorer.seniority_fit("senior", None).value is None
        assert JobScorer.employer(None).value is None

    @pytest.mark.parametrize(
        ("low", "high", "points"),
        [
            (110000, 130000, 100.0),
            (95000, 105000, 85.0),
            (70000, 120000, 50.0),
            (80000, 105000, 70.0),
            (80000, 95000, 30.0),
            (50000, 65000, 0.0),
        ],
    )
    def test_salary_bands_against_floor(
        self, usd_strategy: JobStrategy, low: float, high: float, points: float
    ) -> None:
        item = listing(salary=usd_salary(low, high))
        assert JobScorer.salary_fit(item, usd_strategy).value == points

    def test_undisclosed_salary_is_neutral(self, usd_strategy: JobStrategy) -> None:
        assert JobScorer.salary_fit(listing(), usd_strategy).value == 70.0

    @pytest.mark.parametrize(
        ("posted", "points"),
        [(date(2026, 11, 13), 100.0), (date(2026, 11, 8), 85.0), (date(2026, 10, 1), 55.0)],
    )
    def test_recency(self, posted: date, points: float) -> None:
        assert JobScorer.recency(listing(posted_on=posted), TODAY).value == points


class TestTotal:
    def score_input(self, strategy: JobStrategy, deal_breakers: list[str]) -> JobScoreInput:
        item = listing(eligibility="yes")
        return JobScoreInput(
            listing=item,
            skills=SkillMatcher(strategy).match("Python PostgreSQL Go Kubernetes Kafka"),
            judgment=judgment(deal_breaker_hits=deal_breakers),
            strategy=strategy,
            today=TODAY,
        )

    def test_weighted_mean_over_assessed_axes(self, strategy: JobStrategy) -> None:
        score = JobScorer().score(self.score_input(strategy, []))
        # skill 95x40, seniority and location 100x15 each, salary 70, recency 100, employer 80 x10
        assert score.total == round((3800 + 1500 + 1500 + 700 + 1000 + 800) / 100, 1)
        assert score.blockers == []

    def test_deal_breaker_caps_the_total(self, strategy: JobStrategy) -> None:
        score = JobScorer().score(self.score_input(strategy, ["on-call"]))
        assert score.total == 40.0
        assert score.blockers == ["on-call"]

    def test_zero_weights_are_rejected(self, strategy: JobStrategy) -> None:
        zero = strategy.scoring_weights.model_copy(
            update=dict.fromkeys(strategy.scoring_weights.model_dump(), 0)
        )
        with pytest.raises(ValueError, match="sum to 0"):
            JobScorer().score(
                self.score_input(strategy.model_copy(update={"scoring_weights": zero}), [])
            )
