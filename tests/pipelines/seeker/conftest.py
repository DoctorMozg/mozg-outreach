from datetime import date

import pytest

from mozg_outreach.pipelines.seeker.listings import SourcedListing
from mozg_outreach.pipelines.seeker.models import (
    GIG_WEIGHTS,
    JOB_WEIGHTS,
    Board,
    CvProfile,
    JobBrief,
    JobPreferences,
    JobStrategy,
    KindPlan,
    Listing,
    ListingJudgment,
    Money,
    ScoutedListing,
    SearchScope,
    SeekerBrief,
    SeekerPreferences,
    SeekerStrategy,
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
        "seniority": "senior",
        "counterparty": "established",
        "counterparty_evidence": "Series C, 300 staff",
        "scope_clarity": "partial",
        "scope_evidence": "Responsibilities listed, no start date.",
        "discipline": "engineering",
        "engagement_type": "full_time",
        "deal_breaker_hits": [],
        "summary": "Backend role matching the CV's stack.",
    }
    return ListingJudgment.model_validate(values | fields)


def board(name: str, *kinds: str, **fields: object) -> Board:
    values: dict[str, object] = {
        "name": name,
        "url": f"https://{name.lower()}.example/",
        "type": "global",
        "tier": "canonical",
        "relevance_score": 7,
        "scrape_hints": "",
        "access_notes": "",
        "language_hint": "en",
        "scrape_allowed": True,
        "kinds": list(kinds) or ["job"],
        "disciplines": [],
    }
    return Board.model_validate(values | fields)


@pytest.fixture
def seeker_brief() -> SeekerBrief:
    return SeekerBrief(
        cv_text=CV_TEXT,
        preferences="Remote in the EU, at least EUR 70k or EUR 90 an hour.",
        jobs=SearchScope(recency_days=7, limit=50),
        gigs=SearchScope(recency_days=30, limit=20),
    )


@pytest.fixture
def seeker_strategy(cv: CvProfile) -> SeekerStrategy:
    return SeekerStrategy(
        cv=cv,
        discipline="engineering",
        secondary_disciplines=["data"],
        preferences=SeekerPreferences(
            remote="required",
            regions_allowed=["EU"],
            engagement_types=["full_time", "contract", "project_gig"],
            vetted_only=False,
            excluded_industries=["gambling"],
            excluded_company_attributes=["agency"],
            excluded_companies=["Globex"],
            salary_floor=Money(amount=70000, currency="EUR"),
            rate_floor=Money(amount=90, currency="EUR", period="hour"),
            deal_breakers=["on-call"],
        ),
        work_auth_blockers=["US citizens only"],
        must_have_skills=["Python"],
        must_have_or_groups=[["PostgreSQL", "MySQL"]],
        nice_to_have_skills=["Go", "Kubernetes", "Kafka"],
        synonym_clusters={"PostgreSQL": ["Postgres"], "Kubernetes": ["K8s"]},
        jobs=KindPlan(
            titles=["Backend Engineer"],
            search_queries={"Backend Engineer": ["remote python backend engineer"]},
            bilingual_query_variants={"de": ["Backend Entwickler remote"]},
            scoring_weights=JOB_WEIGHTS,
        ),
        gigs=KindPlan(
            titles=["Backend Consultant"],
            search_queries={"Backend Consultant": ["freelance backend consultant"]},
            bilingual_query_variants={},
            scoring_weights=GIG_WEIGHTS,
        ),
        title_aliases={
            "Backend Engineer": ["Server Engineer"],
            "Backend Consultant": ["API Engineer"],
        },
        scoring_rationale="Defaults.",
        concerns=[],
    )


@pytest.fixture
def cv() -> CvProfile:
    return CvProfile(
        top_titles=["Backend Engineer"],
        skills=["Python", "Go", "PostgreSQL", "Kubernetes"],
        years_experience=9,
        seniority="senior",
        languages_spoken=["English C2", "German B2"],
        current_location="Berlin, Germany",
        country_code="DE",
        work_authorization=["EU citizen"],
    )


@pytest.fixture
def brief() -> JobBrief:
    return JobBrief(cv_text=CV_TEXT, preferences="Remote in the EU, at least EUR 70k.")


@pytest.fixture
def strategy(cv: CvProfile) -> JobStrategy:
    return JobStrategy(
        cv=cv,
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
        scoring_weights=JOB_WEIGHTS,
        scoring_rationale="Defaults.",
        concerns=[],
    )
