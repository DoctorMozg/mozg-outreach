"""Contracts between the freelance pipeline and its agents. A gig is a `Listing` of kind gig."""

from typing import Literal

from pydantic import BaseModel, Field

from mozg_outreach.pipelines.biz.models import CONCERNS_DESCRIPTION
from mozg_outreach.pipelines.seeker.models import (
    CvProfile,
    JobSource,
    Listing,
    Money,
    PlacePreferences,
    SkillRules,
)

Discipline = Literal["engineering", "design", "marketing", "product", "data", "ops", "founder"]
EngagementType = Literal["project_gig", "contractor_placement", "consulting_advisory"]


class FreelanceBrief(BaseModel):
    cv_text: str = Field(min_length=200, description="The CV as plain text or markdown.")
    preferences: str = Field(min_length=1)
    recency_days: int = Field(default=30, ge=1, le=90)
    limit: int = Field(default=50, ge=1, le=200)
    proposals_top_n: int = Field(default=5, ge=0, le=30)


class FreelancePreferences(PlacePreferences):
    engagement_types: list[EngagementType] = Field(min_length=1)
    vetted_only: bool
    excluded_companies: list[str]
    excluded_industries: list[str]
    rate_floor: Money | None = Field(description="Null unless the user named a minimum rate.")
    deal_breakers: list[str]


class FreelanceScoringWeights(BaseModel):
    skill_match: int = Field(default=40, ge=0, le=100)
    location_eligibility: int = Field(default=15, ge=0, le=100)
    scope_clarity: int = Field(default=10, ge=0, le=100)
    budget_fit: int = Field(default=10, ge=0, le=100)
    client_vetting: int = Field(default=10, ge=0, le=100)
    recency: int = Field(default=10, ge=0, le=100)
    discipline_fit: int = Field(default=5, ge=0, le=100)


class FreelanceStrategyInput(BaseModel):
    cv_text: str
    preferences: str
    recency_days: int
    feedback: str | None = Field(default=None, description="The user's notes on a rejected draft.")


class FreelanceStrategy(SkillRules):
    cv: CvProfile
    discipline: Discipline
    secondary_disciplines: list[Discipline] = Field(max_length=2)
    prior_freelance_experience: bool
    preferences: FreelancePreferences
    search_queries: dict[str, list[str]] = Field(description="Queries keyed by target title.")
    title_aliases: dict[str, list[str]]
    bilingual_query_variants: dict[str, list[str]] = Field(
        description="Keyed by language code; empty unless the CV lists a language at B2 or above."
    )
    work_auth_blockers: list[str] = Field(description="Gig phrases that rule the user out.")
    scoring_weights: FreelanceScoringWeights
    scoring_rationale: str
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


class GigScoutInput(BaseModel):
    source: JobSource
    target_titles: list[str]
    queries: list[str]
    title_aliases: list[str]
    skills: list[str]
    discipline: Discipline
    engagement_types: list[EngagementType]
    region_hint: str
    recency_days: int
    max_results: int = Field(ge=1, le=50)


class ScoutedGig(BaseModel):
    title: str = Field(min_length=1)
    client: str = Field(min_length=1, description="Client name or handle; the board if hidden.")
    location_string: str | None
    remote_status_raw: str | None
    url: str = Field(min_length=8)
    posted_date: str | None = Field(description="As shown: ISO date, '3 days ago', 'yesterday'.")
    budget_raw: str | None = Field(description="As shown, e.g. 'EUR 700/day', '$5,000 fixed'.")
    duration_raw: str | None
    scope_raw: str = Field(description="The gig's own scope text, verbatim, up to 1500 chars.")
    application_method_raw: str | None


class GigScoutResult(BaseModel):
    gigs: list[ScoutedGig]
    queries_used: list[str]
    zero_results_verified: bool = Field(
        description="True only when an unfiltered smoke query also found nothing."
    )
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


ScopeClarity = Literal["clear", "partial", "vague"]
ClientVetting = Literal["vetted_platform", "verified_client", "unverified", "red_flags"]


class GigJudgment(BaseModel):
    key: str = Field(description="Copy of the gig listing's `key`.")
    scope_clarity: ScopeClarity
    scope_evidence: str
    client_vetting: ClientVetting
    vetting_evidence: str
    discipline: Discipline | None = Field(description="The gig's discipline; null if unclear.")
    engagement_type: EngagementType | Literal["fractional_retainer"] | None
    deal_breaker_hits: list[str] = Field(description="User deal-breakers the gig mentions.")
    summary: str = Field(description="One sentence on why this gig does or doesn't fit.")


class GigJudgeInput(BaseModel):
    cv: CvProfile
    discipline: Discipline
    deal_breakers: list[str]
    gigs: list[Listing] = Field(min_length=1, max_length=10)


class GigJudgeResult(BaseModel):
    judgments: list[GigJudgment]
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


class GigSourceResearchInput(BaseModel):
    strategy: FreelanceStrategy
    canonical: list[JobSource]
    mode: Literal["freelance"] = "freelance"
