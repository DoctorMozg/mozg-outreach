from collections.abc import Callable
from datetime import date

import pytest

from mozg_outreach.pipelines.seeker.models import (
    GIG_WEIGHTS,
    JOB_WEIGHTS,
    Listing,
    Money,
    Pay,
    SeekerStrategy,
)
from mozg_outreach.pipelines.seeker.pay import PayParser
from mozg_outreach.pipelines.seeker.skills import SkillMatcher
from mozg_outreach.scoring.seeker import ScoreInput, ScoreProfile, SeekerScorer

from .conftest import TODAY, judgment, listing

USD_FLOOR = Money(amount=100000, currency="USD")
GIG_URL = "https://www.malt.com/projects/acme-payments"
SCOPE = "Redesign our payments API in Python and Kafka. Pilot in week 6, cutover in week 8."


def usd_salary(low: float, high: float) -> Pay:
    pay = PayParser().parse(f"${low:,.0f} - ${high:,.0f}", "job")
    assert pay is not None
    return pay


def hourly(low: float, high: float) -> Pay:
    return Pay(
        raw="x", currency="USD", min=low, max=high, basis="hour",
        hourly_min_usd=low, hourly_max_usd=high,
    )  # fmt: skip


def gig(pay: Pay | None = None) -> Listing:
    return listing(key=GIG_URL, url=GIG_URL, kind="gig", summary=SCOPE, pay=pay)


@pytest.fixture
def job_profile(strategy: SeekerStrategy) -> ScoreProfile:
    return ScoreProfile(seniority="senior", weights=JOB_WEIGHTS, pay_floor=USD_FLOOR)


@pytest.fixture
def matcher(strategy: SeekerStrategy) -> SkillMatcher:
    return SkillMatcher(strategy)


@pytest.fixture
def gig_profile() -> ScoreProfile:
    return ScoreProfile(
        seniority="senior",
        weights=GIG_WEIGHTS,
        pay_floor=Money(amount=80, currency="USD", period="hour"),
        engagement_types=["project_gig", "consulting_advisory"],
        discipline="engineering",
        secondary_disciplines=["data"],
    )


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
        self, strategy: SeekerStrategy, text: str, points: float
    ) -> None:
        assert SeekerScorer.skill_match(SkillMatcher(strategy).match(text)).value == points

    @pytest.mark.parametrize(("text", "points"), [("Python, Go, Kafka", 55.0), ("Python", 45.0)])
    def test_partial_must_haves_score_below_complete(
        self, strategy: SeekerStrategy, text: str, points: float
    ) -> None:
        value = SeekerScorer.skill_match(SkillMatcher(strategy).match(text))
        assert value.value == points
        assert "missing: PostgreSQL or MySQL" in value.reason

    def test_skill_match_through_synonyms_only_is_weak(self, strategy: SeekerStrategy) -> None:
        only = strategy.model_copy(update={"must_have_skills": []})
        assert SeekerScorer.skill_match(SkillMatcher(only).match("Postgres")).value == 40.0

    @pytest.mark.parametrize(
        ("asked", "points"),
        [("senior", 100.0), ("mid", 80.0), ("staff", 70.0), ("principal", 50.0), ("intern", 0.0)],
    )
    def test_seniority_gap(self, asked: str, points: float) -> None:
        value = SeekerScorer.seniority_fit("senior", judgment(seniority=asked)).value
        assert value == points

    def test_unjudged_axes_are_unassessed(self, gig_profile: ScoreProfile) -> None:
        assert SeekerScorer.seniority_fit("senior", None).value is None
        assert SeekerScorer.counterparty(None).value is None
        assert SeekerScorer.scope_clarity(None).value is None
        assert SeekerScorer.discipline_fit(None, gig_profile).value is None

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
    def test_salary_bands_against_floor(self, low: float, high: float, points: float) -> None:
        item = listing(pay=usd_salary(low, high))
        assert SeekerScorer.pay_fit(item, USD_FLOOR).value == points

    def test_gig_budget_against_hourly_floor(self) -> None:
        floor = Money(amount=80, currency="USD", period="hour")
        assert SeekerScorer.pay_fit(gig(hourly(72, 88)), floor).value == 100.0
        assert SeekerScorer.pay_fit(gig(hourly(40, 50)), floor).value == 0.0

    def test_unstated_pay_is_unknown(self) -> None:
        assert SeekerScorer.pay_fit(listing(), USD_FLOOR).value == 50.0
        assert SeekerScorer.pay_fit(gig(), USD_FLOOR).value == 50.0

    def test_no_floor_leaves_pay_unassessed(self) -> None:
        assert SeekerScorer.pay_fit(listing(pay=usd_salary(1, 2)), None).value is None
        assert SeekerScorer.pay_fit(gig(hourly(10, 20)), None).value is None

    @pytest.mark.parametrize(
        ("posted", "points"),
        [(date(2026, 11, 13), 100.0), (date(2026, 11, 8), 85.0), (date(2026, 10, 1), 55.0)],
    )
    def test_recency(self, posted: date, points: float) -> None:
        assert SeekerScorer.recency(listing(posted_on=posted), TODAY).value == points

    @pytest.mark.parametrize(
        ("discipline", "points"), [("engineering", 100.0), ("data", 70.0), ("design", 20.0)]
    )
    def test_discipline_fit(
        self, gig_profile: ScoreProfile, discipline: str, points: float
    ) -> None:
        judged = judgment(GIG_URL, discipline=discipline)
        assert SeekerScorer.discipline_fit(judged, gig_profile).value == points

    def test_discipline_fit_needs_a_strategy_discipline(self, job_profile: ScoreProfile) -> None:
        judged = judgment(discipline="engineering")
        assert SeekerScorer.discipline_fit(judged, job_profile).value is None

    @pytest.mark.parametrize(("clarity", "points"), [("clear", 100.0), ("vague", 20.0)])
    def test_scope_clarity(self, clarity: str, points: float) -> None:
        assert SeekerScorer.scope_clarity(judgment(scope_clarity=clarity)).value == points


class TestTotal:
    @pytest.fixture
    def make_input(self, matcher: SkillMatcher) -> Callable[..., ScoreInput]:
        def build(profile: ScoreProfile, item: Listing, **judged: object) -> ScoreInput:
            return ScoreInput(
                listing=item,
                skills=matcher.match("Python PostgreSQL Go Kubernetes Kafka"),
                judgment=judgment(item.key, **judged),
                profile=profile,
                today=TODAY,
            )

        return build

    def test_weighted_mean_over_weighted_axes(
        self, make_input: Callable[..., ScoreInput], job_profile: ScoreProfile
    ) -> None:
        score = SeekerScorer().score(make_input(job_profile, listing(eligibility="yes")))
        # skill 95x40, seniority and location 100x15 each, pay unknown 50, recency 100,
        # counterparty 80 x10; scope and discipline weigh 0 for jobs
        assert score.total == round((3800 + 1500 + 1500 + 500 + 1000 + 800) / 100, 1)
        assert score.blockers == []

    def test_deal_breaker_caps_the_total(
        self, make_input: Callable[..., ScoreInput], job_profile: ScoreProfile
    ) -> None:
        score = SeekerScorer().score(
            make_input(job_profile, listing(), deal_breaker_hits=["on-call"])
        )
        assert score.total == 40.0
        assert score.blockers == ["on-call"]

    def test_unwanted_engagement_type_caps_the_total(
        self, make_input: Callable[..., ScoreInput], gig_profile: ScoreProfile
    ) -> None:
        item = gig(hourly(100, 120))
        score = SeekerScorer().score(
            make_input(gig_profile, item, engagement_type="fractional_retainer")
        )
        assert score.total == 40.0
        assert score.blockers == ["engagement type fractional_retainer is not one the user wants"]

    def test_any_engagement_type_passes_without_a_preference(
        self, make_input: Callable[..., ScoreInput], job_profile: ScoreProfile
    ) -> None:
        score = SeekerScorer().score(make_input(job_profile, listing(), engagement_type="contract"))
        assert score.blockers == []

    def test_clean_gig_scores_high(
        self, make_input: Callable[..., ScoreInput], gig_profile: ScoreProfile
    ) -> None:
        item = gig(hourly(100, 120)).model_copy(update={"eligibility": "yes"})
        judged = {"discipline": "engineering", "engagement_type": "project_gig"}
        score = SeekerScorer().score(make_input(gig_profile, item, **judged))
        assert score.blockers == []
        assert score.total is not None
        assert score.total >= 80
        assert {a.axis for a in score.axes} == set(GIG_WEIGHTS.model_dump())

    def test_zero_weights_are_rejected(
        self, make_input: Callable[..., ScoreInput], job_profile: ScoreProfile
    ) -> None:
        zero = JOB_WEIGHTS.model_copy(update=dict.fromkeys(JOB_WEIGHTS.model_dump(), 0))
        profile = job_profile.model_copy(update={"weights": zero})
        with pytest.raises(ValueError, match="sum to 0"):
            SeekerScorer().score(make_input(profile, listing()))
