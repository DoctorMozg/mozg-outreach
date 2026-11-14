from datetime import date

import pytest

from mozg_outreach.pipelines.job.listings import ListingNormalizer, canonical_url
from mozg_outreach.pipelines.job.models import Money
from mozg_outreach.pipelines.job.salary import USD_PER_UNIT, SalaryParser, annual_usd

from .conftest import TODAY, scouted


class TestSalary:
    @pytest.mark.parametrize(
        ("raw", "low", "high", "period", "currency"),
        [
            ("$120,000 - $150,000", 120000, 150000, "year", "USD"),
            ("EUR 60-80k", 60000, 80000, "year", "EUR"),
            ("£45k to £55k per year", 45000, 55000, "year", "GBP"),
            ("$75/hour", 75, 75, "hour", "USD"),
            ("4,500 EUR per month", 4500, 4500, "month", "EUR"),
        ],
    )
    def test_parses_range_currency_and_period(
        self, raw: str, low: float, high: float, period: str, currency: str
    ) -> None:
        salary = SalaryParser().parse(raw)
        assert salary is not None
        assert (salary.min, salary.max, salary.period, salary.currency) == (
            low,
            high,
            period,
            currency,
        )

    def test_hourly_rate_is_annualized(self) -> None:
        salary = SalaryParser().parse("$50/hour")
        assert salary is not None
        assert salary.annual_min_usd == 50 * 2080

    def test_unknown_currency_keeps_usd_empty(self) -> None:
        salary = SalaryParser().parse("80,000 - 90,000")
        assert salary is not None
        assert salary.annual_min_usd is None

    @pytest.mark.parametrize("raw", [None, "", "Competitive", "DOE"])
    def test_no_amount_is_none(self, raw: str | None) -> None:
        assert SalaryParser().parse(raw) is None

    def test_floor_converts_by_currency_and_period(self) -> None:
        floor = annual_usd(Money(amount=6000, currency="EUR", period="month"))
        assert floor == pytest.approx(6000 * 12 * USD_PER_UNIT["EUR"])


class TestListings:
    def test_tracking_params_and_trailing_slash_are_dropped(self) -> None:
        url = "HTTPS://Jobs.Example.com/role/42/?utm_source=x&gh_jid=42&fbclid=y#apply"
        assert canonical_url(url) == "https://jobs.example.com/role/42?gh_jid=42"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2026-11-10", date(2026, 11, 10)),
            ("Posted 3 days ago", date(2026, 11, 11)),
            ("2 weeks ago", date(2026, 10, 31)),
            ("yesterday", date(2026, 11, 13)),
            ("today", TODAY),
            ("recently", None),
            ("2026-13-40", None),
        ],
    )
    def test_posted_date_forms(self, raw: str, expected: date | None) -> None:
        assert ListingNormalizer(TODAY).posted_on(raw) == expected

    def test_same_url_from_two_sources_merges(self) -> None:
        listings = ListingNormalizer(TODAY).normalize(
            [scouted("Remotive"), scouted("RemoteOK", url="https://jobs.acme.example/backend/")]
        )
        assert len(listings) == 1
        assert listings[0].sources == ["Remotive", "RemoteOK"]

    def test_repost_on_another_host_keeps_the_earliest(self) -> None:
        listings = ListingNormalizer(TODAY).normalize(
            [
                scouted("Arbeitnow", url="https://arbeitnow.example/acme-be", posted_date="today"),
                scouted(
                    "Greenhouse",
                    title="Backend Engineer",
                    company="Acme",
                    url="https://boards.greenhouse.io/acme/1",
                    posted_date="2026-11-01",
                ),
            ]
        )
        assert len(listings) == 1
        assert listings[0].url == "https://boards.greenhouse.io/acme/1"
        assert listings[0].merged_urls == ["https://arbeitnow.example/acme-be"]
        assert set(listings[0].sources) == {"Arbeitnow", "Greenhouse"}

    def test_posts_without_a_company_never_merge_by_title(self) -> None:
        listings = ListingNormalizer(TODAY).normalize(
            [
                scouted("telegram", company="", url="https://t.me/py_jobs/1"),
                scouted("telegram", company="", url="https://t.me/go_jobs/7"),
            ]
        )
        assert len(listings) == 2

    def test_different_roles_stay_apart(self) -> None:
        listings = ListingNormalizer(TODAY).normalize(
            [scouted(), scouted(title="Data Engineer", url="https://jobs.acme.example/data")]
        )
        assert len(listings) == 2
