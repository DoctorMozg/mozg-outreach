from datetime import date

import pytest

from mozg_outreach.pipelines.seeker.listings import SourcedListing
from mozg_outreach.pipelines.seeker.models import (
    CvProfile,
    JobBrief,
    JobPreferences,
    JobScoringWeights,
    JobStrategy,
    Listing,
    ListingJudgment,
    Money,
    ScoutedListing,
)

TODAY = date(2026, 11, 14)
CV_TEXT = "Senior backend engineer in Berlin. Python, Go, PostgreSQL, Kubernetes. " * 4


def scouted(source: str = "Remotive", **fields: object) -> SourcedListing:
    values: dict[str, object] = {
        "title": "Senior Backend Engineer",
        "company": "Acme GmbH",
        "location_string": "Remote (EU)",
        "remote_status_raw": "Remote",
        "url": "https://jobs.acme.example/backend?utm_source=remotive",
        "posted_date": "2026-11-12",
        "salary_string": "EUR 80,000 - 95,000",
        "summary_snippet": "Python and PostgreSQL services on Kubernetes. Remote within the EU.",
    }
    listing = ScoutedListing.model_validate(values | fields)
    return SourcedListing(source=source, kind="job", listing=listing)


# Greenhouse postings open with about 3,000 characters of company text before the requirements.
BOILERPLATE = "\n".join(
    f"About Acme, part {n}: our mission, values and benefits." for n in range(60)
)
REQUIREMENTS = "- 5+ years of Python services on PostgreSQL"
LONG_POSTING = f"{BOILERPLATE}\n{REQUIREMENTS}\n- Remote within the EU"


def listing(**fields: object) -> Listing:
    values: dict[str, object] = {
        "key": "https://jobs.acme.example/backend",
        "kind": "job",
        "title": "Senior Backend Engineer",
        "company": "Acme GmbH",
        "location_string": "Remote (EU)",
        "remote_status_raw": "Remote",
        "url": "https://jobs.acme.example/backend",
        "sources": ["Remotive"],
        "posted_on": date(2026, 11, 12),
        "pay": None,
        "summary": "Python and PostgreSQL services on Kubernetes.",
    }
    return Listing.model_validate(values | fields)


def judgment(key: str = "https://jobs.acme.example/backend", **fields: object) -> ListingJudgment:
    values: dict[str, object] = {
        "key": key,
        "listing_seniority": "senior",
        "employer_signal": "established",
        "employer_evidence": "Series C, 300 staff",
        "deal_breaker_hits": [],
        "summary": "Backend role matching the CV's stack.",
    }
    return ListingJudgment.model_validate(values | fields)


@pytest.fixture
def brief() -> JobBrief:
    return JobBrief(cv_text=CV_TEXT, preferences="Remote in the EU, at least EUR 70k.")


@pytest.fixture
def strategy() -> JobStrategy:
    return JobStrategy(
        cv=CvProfile(
            top_titles=["Backend Engineer"],
            skills=["Python", "Go", "PostgreSQL", "Kubernetes"],
            years_experience=9,
            seniority="senior",
            languages_spoken=["English C2", "German B2"],
            current_location="Berlin, Germany",
            country_code="DE",
            work_authorization=["EU citizen"],
        ),
        preferences=JobPreferences(
            remote="required",
            regions_allowed=["EU"],
            excluded_industries=["gambling"],
            excluded_company_attributes=["agency"],
            salary_floor=Money(amount=70000, currency="EUR"),
            deal_breakers=["on-call"],
        ),
        search_queries={"Backend Engineer": ["remote python backend engineer"]},
        title_aliases={"Backend Engineer": ["Server Engineer"]},
        bilingual_query_variants={"de": ["Backend Entwickler remote"]},
        must_have_skills=["Python"],
        must_have_or_groups=[["PostgreSQL", "MySQL"]],
        nice_to_have_skills=["Go", "Kubernetes", "Kafka"],
        synonym_clusters={"PostgreSQL": ["Postgres"], "Kubernetes": ["K8s"]},
        work_auth_blockers=["US citizens only"],
        scoring_weights=JobScoringWeights(),
        scoring_rationale="Defaults.",
        concerns=[],
    )
