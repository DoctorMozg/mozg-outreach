from datetime import date

import pytest

from mozg_outreach.pipelines.seeker.listings import ListingNormalizer, canonical_url
from mozg_outreach.pipelines.seeker.models import Money
from mozg_outreach.pipelines.seeker.pay import USD_PER_UNIT, PayParser, hourly_usd, project_hours

from .conftest import TODAY, scouted

EUR = USD_PER_UNIT["EUR"]


class TestPay:
    @pytest.mark.parametrize(
        ("raw", "low", "high", "basis", "currency"),
        [
            ("$120,000 - $150,000", 120000, 150000, "year", "USD"),
            ("EUR 60-80k", 60000, 80000, "year", "EUR"),
            ("£45k to £55k per year", 45000, 55000, "year", "GBP"),
            ("$75/hour", 75, 75, "hour", "USD"),
            ("4,500 EUR per month", 4500, 4500, "month", "EUR"),
        ],
    )
    def test_parses_range_currency_and_basis(
        self, raw: str, low: float, high: float, basis: str, currency: str
    ) -> None:
        pay = PayParser().parse(raw, "job")
        assert pay is not None
        assert (pay.min, pay.max, pay.basis, pay.currency) == (low, high, basis, currency)

    @pytest.mark.parametrize(
        ("raw", "duration", "basis", "hourly"),
        [
            ("$104,000 per year", None, "year", 50.0),
            ("EUR 800/day", None, "day", 100 * EUR),
            ("$60-80/hr", None, "hour", 60.0),
            ("$6,400 fixed price", "4 weeks", "project", 40.0),
            ("$9,600 project", None, "project", 30.0),
        ],
    )
    def test_hourly_usd_for_every_basis(
        self, raw: str, duration: str | None, basis: str, hourly: float
    ) -> None:
        pay = PayParser().parse(raw, "gig", duration)
        assert pay is not None
        assert pay.basis == basis
        assert pay.hourly_min_usd == pytest.approx(hourly, abs=0.01)

    def test_only_a_gig_budget_covers_a_project(self) -> None:
        pay = PayParser().parse("$150,000 total compensation", "job")
        assert pay is not None
        assert pay.basis == "year"
        assert pay.hourly_min_usd == pytest.approx(150000 / 2080, abs=0.01)

    def test_unknown_currency_keeps_usd_empty(self) -> None:
        pay = PayParser().parse("80,000 - 90,000", "job")
        assert pay is not None
        assert pay.hourly_min_usd is None

    @pytest.mark.parametrize("raw", [None, "", "Competitive", "DOE", "Negotiable"])
    def test_no_amount_is_none(self, raw: str | None) -> None:
        assert PayParser().parse(raw, "gig") is None

    @pytest.mark.parametrize(
        ("raw", "hours"), [("3 months", 520), ("2 weeks", 80), ("10 days", 80), (None, 320)]
    )
    def test_project_hours(self, raw: str | None, hours: float) -> None:
        assert project_hours(raw) == pytest.approx(hours)

    @pytest.mark.parametrize(
        ("amount", "period", "hourly"),
        [(160, "day", 20.0), (104000, "year", 50.0), (52000 / 12, "month", 25.0)],
    )
    def test_floor_per_period(self, amount: float, period: str, hourly: float) -> None:
        money = Money.model_validate({"amount": amount, "currency": "USD", "period": period})
        assert hourly_usd(money) == pytest.approx(hourly, abs=0.01)


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

    def test_telegram_reposts_merge_whatever_place_the_parser_read(self) -> None:
        listings = ListingNormalizer(TODAY).normalize(
            [
                scouted("telegram", url="https://t.me/py_jobs/1", location_string="Moscow"),
                scouted("telegram", url="https://t.me/go_jobs/7", remote_status_raw="Remote"),
                scouted(
                    url="https://b.example/1", location_string="Berlin", remote_status_raw=None
                ),
            ]
        )
        assert sorted(len(x.merged_urls) for x in listings) == [0, 1]

    def test_posts_linking_one_vacancy_page_merge(self) -> None:
        vacancy = "https://career.habr.com/vacancies/1?utm_source=tg"
        listings = ListingNormalizer(TODAY).normalize(
            [
                scouted("telegram", url="https://t.me/a/1", title="Go dev", apply_url=vacancy),
                scouted("telegram", url="https://t.me/b/2", title="Golang", apply_url=vacancy),
                scouted("Habr", url="https://career.habr.com/vacancies/1", title="Go"),
            ]
        )
        assert len(listings) == 1
        assert set(listings[0].sources) == {"telegram", "Habr"}

    def test_different_roles_stay_apart(self) -> None:
        listings = ListingNormalizer(TODAY).normalize(
            [scouted(), scouted(title="Data Engineer", url="https://jobs.acme.example/data")]
        )
        assert len(listings) == 2
