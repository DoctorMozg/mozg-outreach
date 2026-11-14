import pytest

from mozg_outreach.domain import FieldDefinition, FieldSpec, FieldType, FieldValue
from mozg_outreach.pipelines.biz.assessment_models import FitVerdict, SignalCheck, SignalKind
from mozg_outreach.pipelines.biz.enrichment_models import (
    CompanyDossier,
    ContactsResult,
    EmailContact,
    GrowthResult,
    PlatformReview,
    ReputationResult,
)
from mozg_outreach.pipelines.biz.models import ScoringWeights, SearchSignals
from mozg_outreach.scoring.biz import BizScoreInput, BizScorer
from tests.pipelines.biz.conftest import load_fixture

SIGNALS = SearchSignals(
    must_have=["B2B", "runs on Kubernetes"],
    strong_positive=["hiring platform engineers"],
    weak_positive=[],
    disqualifying=["in-house FinOps team"],
)
EQUAL = ScoringWeights(
    data_completeness=1,
    review_reputation=1,
    contact_availability=1,
    growth_signals=1,
    sector_relevance=1,
    outreach_feasibility=1,
)

REQUIRED_SPEC = FieldSpec(
    fields=[
        FieldDefinition(
            id="cloud", label="Cloud", type=FieldType.LIST, description="d", required=True
        ),
        FieldDefinition(
            id="k8s", label="K8s", type=FieldType.BOOLEAN, description="d", required=True
        ),
    ]
)
HALF_FILLED = {"cloud": FieldValue(value=["AWS"], source_url="https://acme.io/jobs")}


@pytest.fixture(scope="module")
def dossier() -> CompanyDossier:
    return CompanyDossier(
        reputation=load_fixture("enrichment/reputation", ReputationResult),
        contacts=load_fixture("enrichment/contacts", ContactsResult),
        growth=load_fixture("enrichment/growth", GrowthResult),
    )


def check(signal: str, kind: SignalKind, present: str) -> SignalCheck:
    return SignalCheck.model_validate(
        {"signal": signal, "kind": kind, "present": present, "evidence": None, "source_url": None}
    )


def verdict(*checks: SignalCheck, sector: str = "exact") -> FitVerdict:
    return FitVerdict.model_validate(
        {"sector_match": sector, "sector_reason": "core SaaS", "signals": checks, "concerns": []}
    )


def score_input(
    dossier: CompanyDossier | None, fit: FitVerdict | None, **extra: object
) -> BizScoreInput:
    data: dict[str, object] = {
        "dossier": dossier,
        "verdict": fit,
        "signals": SIGNALS,
        "fields": {},
        "spec": None,
        "weights": EQUAL,
    }
    return BizScoreInput.model_validate({**data, **extra})


def axis(data: BizScoreInput, name: str) -> float | None:
    return next(a.value for a in BizScorer().score(data).axes if a.axis == name)


class TestTotal:
    def test_total_is_weighted_mean_of_axes(self, dossier: CompanyDossier) -> None:
        weights = EQUAL.model_copy(update={"contact_availability": 5, "review_reputation": 0})
        result = BizScorer().score(score_input(dossier, verdict(), weights=weights))
        assessed = [a for a in result.axes if a.value is not None]
        assert len(assessed) == 6
        expected = sum((a.value or 0) * a.weight for a in assessed) / sum(
            a.weight for a in assessed
        )
        assert result.total == pytest.approx(round(expected, 1))
        assert [a.axis for a in result.axes] == list(EQUAL.model_dump())
        assert all(a.reason for a in result.axes)

    def test_zero_weights_rejected(self, dossier: CompanyDossier) -> None:
        zero = ScoringWeights.model_validate(dict.fromkeys(EQUAL.model_dump(), 0))
        with pytest.raises(ValueError, match="sum to 0"):
            BizScorer().score(score_input(dossier, None, weights=zero))


class TestNotEnriched:
    def test_enrichment_axes_are_left_out_of_total(self, dossier: CompanyDossier) -> None:
        fit = verdict(
            check("B2B", "must_have", "yes"), check("runs on Kubernetes", "must_have", "yes")
        )
        result = BizScorer().score(score_input(None, fit))
        values = {a.axis: a.value for a in result.axes}
        assert values["review_reputation"] is None
        assert values["contact_availability"] is None
        assert values["growth_signals"] is None
        # No required fields, so completeness is unassessed too.
        assert values["data_completeness"] is None
        # Sector exact (100); feasibility 2/2 must-haves (70) + 0/1 strong positives (0).
        assert result.total == 85.0

    def test_completeness_counts_required_fields_only(self, dossier: CompanyDossier) -> None:
        data = score_input(None, None, spec=REQUIRED_SPEC, fields=HALF_FILLED)
        assert axis(data, "data_completeness") == 50.0

    def test_total_is_none_when_only_enrichment_axes_weigh(self, dossier: CompanyDossier) -> None:
        weights = ScoringWeights.model_validate(
            {**dict.fromkeys(EQUAL.model_dump(), 0), "growth_signals": 1}
        )
        result = BizScorer().score(score_input(None, None, weights=weights))
        assert result.total is None


class TestReputation:
    @pytest.mark.parametrize(
        ("ratings", "expected"),
        [
            ([(5.0, 10)], 100.0),
            ([(4.0, 3), (4.0, 7)], 80.0),
            ([(5.0, 1), (3.0, 3)], 70.0),
            ([(2.5, 40)], 40.0),
            ([], 20.0),
        ],
    )
    def test_count_weighted_average_maps_to_points(
        self, dossier: CompanyDossier, ratings: list[tuple[float, int]], expected: float
    ) -> None:
        assert dossier.reputation is not None
        empty = PlatformReview(
            rating=None, count=0, sentiment="no_data", notable=None, source_url=None
        )
        platforms = ["glassdoor", "trustpilot", "indeed", "google_business"]
        reviews = {p: empty for p in platforms}
        for platform, (rating, count) in zip(platforms, ratings, strict=False):
            reviews[platform] = empty.model_copy(update={"rating": rating, "count": count})
        reputation = dossier.reputation.model_copy(
            update={"reviews": dossier.reputation.reviews.model_copy(update=reviews)}
        )
        data = score_input(dossier.model_copy(update={"reputation": reputation}), None)
        assert axis(data, "review_reputation") == pytest.approx(expected)


class TestContacts:
    def test_named_people_count_without_linkedin(self, dossier: CompanyDossier) -> None:
        assert dossier.contacts is not None
        person = dossier.contacts.key_people[0].model_copy(update={"linkedin": None, "email": None})
        people = [person, person.model_copy(update={"name": "Second Person"})]
        bare = dossier.contacts.model_copy(
            update={
                "key_people": people,
                "emails": [],
                "phones": [],
                "social": dossier.contacts.social.model_copy(
                    update={"linkedin_company": None, "twitter": None}
                ),
            }
        )
        data = score_input(dossier.model_copy(update={"contacts": bare}), None)
        assert axis(data, "contact_availability") == 50.0

    def test_guessed_email_counts_less_than_published(self, dossier: CompanyDossier) -> None:
        assert dossier.contacts is not None
        guess = EmailContact(
            address="info@acme.io", kind="pattern_guess", source_url="https://acme.io"
        )
        bare = dossier.contacts.model_copy(
            update={
                "emails": [guess],
                "key_people": [],
                "phones": [],
                "social": dossier.contacts.social.model_copy(
                    update={"linkedin_company": None, "twitter": None}
                ),
            }
        )
        published = bare.model_copy(
            update={"emails": [guess.model_copy(update={"kind": "published"})]}
        )
        assert (
            axis(
                score_input(dossier.model_copy(update={"contacts": bare}), None),
                "contact_availability",
            )
            == 5.0
        )
        assert (
            axis(
                score_input(dossier.model_copy(update={"contacts": published}), None),
                "contact_availability",
            )
            == 20.0
        )


class TestGrowth:
    @pytest.mark.parametrize(
        "trajectory", ["rapid_growth", "steady_growth", "stable", "declining", "pivoting"]
    )
    def test_every_trajectory_is_scored(self, dossier: CompanyDossier, trajectory: str) -> None:
        assert dossier.growth is not None
        quiet = dossier.growth.model_copy(
            update={
                "trajectory": trajectory,
                "funding": None,
                "job_postings": dossier.growth.job_postings.model_copy(update={"total_open": 0}),
            }
        )
        value = axis(
            score_input(dossier.model_copy(update={"growth": quiet}), None), "growth_signals"
        )
        assert value is not None
        assert 10.0 <= value <= 80.0


class TestFit:
    def test_disqualifier_zeroes_feasibility(self, dossier: CompanyDossier) -> None:
        fit = verdict(check("in-house FinOps team", "disqualifying", "yes"))
        result = BizScorer().score(score_input(dossier, fit))
        assert result.blockers == ["in-house FinOps team"]
        assert next(a for a in result.axes if a.axis == "outreach_feasibility").value == 0.0

    def test_skipped_must_have_counts_as_unknown(self, dossier: CompanyDossier) -> None:
        fit = verdict(
            check("B2B", "must_have", "yes"),
            check("hiring platform engineers", "strong_positive", "yes"),
        )
        # Must-haves: B2B yes (1) + Kubernetes skipped (0.5) of 2 -> 70 * 0.75; strong 1/1 -> 30.
        assert axis(score_input(dossier, fit), "outreach_feasibility") == 82.5

    def test_failed_must_have_caps_feasibility(self, dossier: CompanyDossier) -> None:
        fit = verdict(
            check("B2B", "must_have", "yes"),
            check("runs on Kubernetes", "must_have", "no"),
            check("hiring platform engineers", "strong_positive", "yes"),
        )
        result = BizScorer().score(score_input(dossier, fit))
        feasibility = next(a for a in result.axes if a.axis == "outreach_feasibility")
        assert feasibility.value == 40.0
        assert "fails: runs on Kubernetes" in feasibility.reason
        assert result.blockers == []

    def test_unjudged_fit_is_neutral(self, dossier: CompanyDossier) -> None:
        data = score_input(dossier, None)
        assert axis(data, "outreach_feasibility") == 50.0
        assert axis(data, "sector_relevance") == 50.0

    def test_sector_match_points(self, dossier: CompanyDossier) -> None:
        assert axis(score_input(dossier, verdict(sector="adjacent")), "sector_relevance") == 60.0


def test_required_fields_raise_completeness(dossier: CompanyDossier) -> None:
    # 3 of 5 sections (0.6 * 0.6) + 1 of 2 required fields (0.4 * 0.5) -> 56.
    data = score_input(dossier, None, spec=REQUIRED_SPEC, fields=HALF_FILLED)
    assert axis(data, "data_completeness") == 56.0
