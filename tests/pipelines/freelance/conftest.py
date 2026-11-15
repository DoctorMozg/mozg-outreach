import pytest

from mozg_outreach.pipelines.freelance.models import (
    FreelanceBrief,
    FreelancePreferences,
    FreelanceStrategy,
    ScoutedGig,
)
from mozg_outreach.pipelines.freelance.search import SourcedGig
from mozg_outreach.pipelines.seeker.models import (
    GIG_WEIGHTS,
    CvProfile,
    GigTerms,
    Listing,
    Money,
    Pay,
)
from tests.pipelines.seeker.conftest import CV_TEXT, listing

GIG_URL = "https://www.malt.com/projects/acme-payments"
SCOPE = (
    "We need a backend engineer to redesign our payments API in Python. "
    "Scope: migrate from synchronous REST to event-driven Kafka on PostgreSQL. "
    "Deliver a working pilot in week 6 and the full cutover in week 8."
)


def scouted_gig(**fields: object) -> SourcedGig:
    values: dict[str, object] = {
        "title": "Payments API redesign",
        "client": "AcmeCo",
        "location_string": "Remote (EU)",
        "remote_status_raw": "Remote",
        "url": f"{GIG_URL}?utm_source=x",
        "posted_date": "2026-11-10",
        "budget_raw": "EUR 700/day",
        "duration_raw": "8 weeks",
        "scope_raw": SCOPE,
        "application_method_raw": "Apply via Malt.",
    }
    return SourcedGig(source="Malt", gig=ScoutedGig.model_validate(values | fields))


def gig(budget: Pay | None = None, company: str = "AcmeCo") -> Listing:
    terms = GigTerms(duration_raw="8 weeks", application_method="Apply via Malt.")
    return listing(
        key=GIG_URL,
        kind="gig",
        url=GIG_URL,
        company=company,
        summary=SCOPE,
        sources=["Malt"],
        pay=budget,
        terms=terms,
    )


@pytest.fixture
def brief() -> FreelanceBrief:
    return FreelanceBrief(cv_text=CV_TEXT, preferences="Remote EU gigs, at least EUR 80 an hour.")


@pytest.fixture
def strategy() -> FreelanceStrategy:
    return FreelanceStrategy(
        cv=CvProfile(
            top_titles=["Backend Engineer"],
            skills=["Python", "Go", "PostgreSQL", "Kafka"],
            years_experience=9,
            seniority="senior",
            languages_spoken=["English C2"],
            current_location="Berlin, Germany",
            country_code="DE",
            work_authorization=["EU citizen"],
        ),
        discipline="engineering",
        secondary_disciplines=["data"],
        prior_freelance_experience=False,
        preferences=FreelancePreferences(
            remote="required",
            regions_allowed=["EU"],
            engagement_types=["project_gig", "consulting_advisory"],
            vetted_only=False,
            excluded_companies=["BadCorp"],
            excluded_industries=["gambling"],
            rate_floor=Money(amount=80, currency="EUR", period="hour"),
            deal_breakers=["unpaid test task"],
        ),
        search_queries={"Backend Engineer": ["freelance backend engineer python"]},
        title_aliases={"Backend Engineer": ["Backend Consultant"]},
        bilingual_query_variants={},
        must_have_skills=["Python"],
        must_have_or_groups=[],
        nice_to_have_skills=["Kafka", "Go"],
        synonym_clusters={"PostgreSQL": ["Postgres"]},
        work_auth_blockers=[],
        scoring_weights=GIG_WEIGHTS,
        scoring_rationale="Defaults.",
        concerns=[],
    )
