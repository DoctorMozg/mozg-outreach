import pytest

from mozg_outreach.pipelines.seeker.eligibility import EligibilityChecker, places_in
from mozg_outreach.pipelines.seeker.models import SeekerStrategy
from mozg_outreach.pipelines.seeker.screening import ListingScreener, ScreenRules
from mozg_outreach.pipelines.seeker.skills import SkillMatcher, _pattern

from .conftest import TODAY, listing, scouted


@pytest.fixture
def checker(strategy: SeekerStrategy) -> EligibilityChecker:
    return EligibilityChecker(strategy.cv, strategy.preferences, strategy.work_auth_blockers)


class TestEligibility:
    @pytest.mark.parametrize(
        ("location", "remote", "summary", "verdict"),
        [
            ("Remote (EU)", "Remote", "", "yes"),
            ("Remote", "Remote", "Work from anywhere.", "yes"),
            ("Remote - US", "Remote", "", "no"),
            ("Remote", "Remote", "", "unknown"),
            ("Remote", "Remote", "Must be located in the United Kingdom.", "no"),
            ("Remote", "Remote", "Must be based in Germany.", "yes"),
            ("Berlin, Germany", None, "", "yes"),
            ("London, UK", None, "Hybrid, 3 days in office.", "no"),
            ("Remote", "Remote", "US citizens only.", "no"),
        ],
    )
    def test_verdicts(
        self,
        checker: EligibilityChecker,
        location: str,
        remote: str | None,
        summary: str,
        verdict: str,
    ) -> None:
        item = listing(location_string=location, remote_status_raw=remote, summary=summary)
        assert checker.check(item).verdict == verdict

    def test_lowercase_us_is_not_the_country(self) -> None:
        assert places_in("join us remotely") == frozenset()
        assert places_in("Remote, US only") == frozenset({"US"})


class TestSkills:
    @pytest.mark.parametrize(
        ("term", "text", "found"),
        [
            ("Go", "We use Go and Rust", True),
            ("Go", "go to market", False),
            ("Go", "Golang", False),
            ("C++", "Modern C++17", True),
            ("C", "C++ developer", False),
            ("K8s", "EKS/K8s clusters", True),
            ("Rust", "rusty pipes", False),
        ],
    )
    def test_term_boundaries(self, term: str, text: str, found: bool) -> None:
        assert bool(_pattern(term).search(text)) is found

    def test_synonym_satisfies_an_or_group(self, strategy: SeekerStrategy) -> None:
        match = SkillMatcher(strategy).match("Python on Postgres")
        assert match.gate_passed
        assert match.or_groups[0].term == "Postgres"
        assert not match.or_groups[0].canonical

    def test_partial_match_passes_the_gate(self, strategy: SeekerStrategy) -> None:
        match = SkillMatcher(strategy).match("Java and MySQL")
        assert match.missing_must_haves == ["Python"]
        assert match.gate_passed
        assert not match.complete

    def test_no_required_skill_fails_the_gate(self, strategy: SeekerStrategy) -> None:
        assert not SkillMatcher(strategy).match("Java and Oracle").gate_passed


# Long enough to be a full posting, so naming no required skill excludes it.
JAVA_POSTING = "We build our services in Java and Oracle. " * 12


class TestScreener:
    def test_kept_listings_come_newest_first(self, strategy: SeekerStrategy) -> None:
        screening = ListingScreener(ScreenRules.for_seeker(strategy, 7), TODAY).screen(
            [
                scouted("telegram", url="https://t.me/a/1", title="Old", posted_date="2026-11-09"),
                scouted(url="https://a.example/2", title="New", posted_date="2026-11-14"),
                scouted(url="https://a.example/3", title="Undated", posted_date=None),
            ]
        )
        assert [item.title for item in screening.kept] == ["New", "Old", "Undated"]

    def test_a_snippet_too_short_to_name_a_stack_goes_on(self, strategy: SeekerStrategy) -> None:
        snippet = scouted(
            url="https://a.example/5", title="Backend", summary_snippet="Remote role."
        )

        screening = ListingScreener(ScreenRules.for_seeker(strategy, 7), TODAY).screen([snippet])

        assert [item.key for item in screening.kept] == ["https://a.example/5"]

    def test_each_gate_names_its_reason(self, strategy: SeekerStrategy) -> None:
        screening = ListingScreener(ScreenRules.for_seeker(strategy, 7), TODAY).screen(
            [
                scouted(),
                scouted(url="https://a.example/1", title="Old", posted_date="2026-10-01"),
                scouted(url="https://a.example/2", company="BetCo", summary_snippet="gambling"),
                scouted(url="https://a.example/3", title="QA", location_string="Remote - US"),
                scouted(url="https://a.example/4", title="Java", summary_snippet=JAVA_POSTING),
            ]
        )
        assert [item.key for item in screening.kept] == ["https://jobs.acme.example/backend"]
        assert screening.kept[0].eligibility == "yes"
        reasons = {e.listing.url: (e.reason, e.detail) for e in screening.excluded}
        assert reasons == {
            "https://a.example/1": ("stale", "posted 2026-10-01"),
            "https://a.example/2": ("industry_blocklist", "gambling"),
            "https://a.example/3": ("location_mismatch", "Remote - US"),
            "https://a.example/4": (
                "missing_must_have",
                "names none of: Python, PostgreSQL or MySQL",
            ),
        }
