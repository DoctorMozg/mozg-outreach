"""Contracts between the job-search pipeline and its agents, and the normalized listings."""

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from mozg_outreach.pipelines.biz.models import CONCERNS_DESCRIPTION

Seniority = Literal["intern", "junior", "mid", "senior", "staff", "principal", "executive"]
RemotePreference = Literal["required", "preferred", "no"]
Period = Literal["year", "month", "day", "hour"]
PayBasis = Literal["year", "month", "day", "hour", "project"]
ListingKind = Literal["job", "gig"]
# One vocabulary for every excluded job or gig, so the report can explain each exclusion.
ExcludedReason = Literal[
    "stale",
    "industry_blocklist",
    "attribute_blocklist",
    "location_mismatch",
    "missing_must_have",
    "salary_below_floor",
]


class JobBrief(BaseModel):
    """What the user gives a job project: their CV text and what they want."""

    cv_text: str = Field(min_length=200, description="The CV as plain text or markdown.")
    preferences: str = Field(min_length=1)
    recency_days: int = Field(default=7, ge=1, le=90)
    limit: int = Field(default=50, ge=1, le=200)
    contacts_top_n: int = Field(default=10, ge=0, le=50)
    letters_top_n: int = Field(default=10, ge=0, le=50)


class Money(BaseModel):
    amount: float = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3, description="ISO 4217 code, e.g. EUR.")
    period: Period = "year"


AGENT_CHARS = 4000
OPENING_CHARS = 800


class CvProfile(BaseModel):
    top_titles: list[str] = Field(min_length=1)
    skills: list[str]
    years_experience: int | None = Field(ge=0)
    seniority: Seniority
    languages_spoken: list[str] = Field(description="With CEFR level, e.g. 'German B2'.")
    current_location: str = Field(description="City and country, e.g. 'Berlin, Germany'.")
    country_code: str = Field(min_length=2, max_length=2, description="ISO 3166-1 alpha-2.")
    work_authorization: list[str]


class PlacePreferences(BaseModel):
    remote: RemotePreference
    regions_allowed: list[str] = Field(description="Region codes like EU, EMEA, US or 'anywhere'.")


class JobPreferences(PlacePreferences):
    employment_type: Literal["full-time", "part-time", "contract", "any"] = "any"
    excluded_industries: list[str]
    excluded_company_attributes: list[str]
    salary_floor: Money | None = Field(description="Null unless the user named a minimum.")
    deal_breakers: list[str]


class JobScoringWeights(BaseModel):
    skill_match: int = Field(default=40, ge=0, le=100)
    seniority_fit: int = Field(default=15, ge=0, le=100)
    location_eligibility: int = Field(default=15, ge=0, le=100)
    salary_fit: int = Field(default=10, ge=0, le=100)
    recency: int = Field(default=10, ge=0, le=100)
    employer_signals: int = Field(default=10, ge=0, le=100)


class SkillRules(BaseModel):
    must_have_skills: list[str]
    must_have_or_groups: list[list[str]]
    nice_to_have_skills: list[str]
    synonym_clusters: dict[str, list[str]] = Field(description="Canonical skill to its synonyms.")


class JobStrategyInput(BaseModel):
    cv_text: str
    preferences: str
    recency_days: int
    feedback: str | None = Field(default=None, description="The user's notes on a rejected draft.")


class JobStrategy(SkillRules):
    cv: CvProfile
    preferences: JobPreferences
    search_queries: dict[str, list[str]] = Field(description="Queries keyed by target title.")
    title_aliases: dict[str, list[str]]
    bilingual_query_variants: dict[str, list[str]] = Field(
        description="Keyed by language code; empty unless the CV lists a language at B2 or above."
    )
    work_auth_blockers: list[str] = Field(description="Listing phrases that rule the user out.")
    scoring_weights: JobScoringWeights
    scoring_rationale: str
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)

    def skills(self) -> list[str]:
        """Must-haves first, then the CV's skills in its order, which leads with the core stack."""
        grouped = [s for group in self.must_have_or_groups for s in group]
        return list(dict.fromkeys([*self.must_have_skills, *grouped, *self.cv.skills]))


class JobSource(BaseModel):
    name: str
    url: str
    type: str = Field(description="Free label: global, regional, niche, ATS, guidance...")
    tier: Literal["canonical", "augmented"]
    relevance_score: int = Field(ge=1, le=10)
    scrape_hints: str = ""
    access_notes: str = ""
    language_hint: str = "en"
    scrape_allowed: bool = True
    discipline: str | None = Field(default=None, description="Freelance boards: comma list.")
    guidance_note: str | None = Field(default=None, description="How to get in, for gated sites.")


class SourceResearchInput(BaseModel):
    strategy: JobStrategy
    canonical: list[JobSource]
    mode: Literal["job"] = "job"


class SourcePlan(BaseModel):
    augmented: list[JobSource]
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


class JobScoutInput(BaseModel):
    source: JobSource
    target_titles: list[str]
    queries: list[str]
    title_aliases: list[str]
    skills: list[str]
    region_hint: str
    recency_days: int
    max_results: int = Field(ge=1, le=50)


class TelegramContact(BaseModel):
    """The person a Telegram post says to write to, checked against the post itself."""

    handle: str = Field(description="Normalized username, without '@'.")
    name: str | None = Field(
        description="As the post writes it; null when only the handle is given."
    )
    quote: str = Field(description="The post's line that names the contact.")


class ScoutedListing(BaseModel):
    title: str = Field(min_length=1)
    company: str = Field(description="The employer; empty only when the listing names none.")
    location_string: str | None
    remote_status_raw: str | None
    url: str = Field(min_length=8)
    posted_date: str | None = Field(description="As shown: ISO date, '3 days ago', 'yesterday'.")
    salary_string: str | None
    summary_snippet: str
    apply_url: str | None = Field(default=None, description="Vacancy page a post links to.")
    telegram_contact: TelegramContact | None = None
    duration_raw: str | None = None
    application_method_raw: str | None = None


class JobScoutResult(BaseModel):
    listings: list[ScoutedListing]
    queries_used: list[str]
    zero_results_verified: bool = Field(
        description="True only when an unfiltered smoke query also found nothing."
    )
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


class Pay(BaseModel):
    """A salary or a gig budget; USD fields stay empty for an unknown currency."""

    raw: str
    currency: str | None
    min: float
    max: float
    basis: PayBasis
    hourly_min_usd: float | None
    hourly_max_usd: float | None


class GigTerms(BaseModel):
    duration_raw: str | None
    application_method: str | None


class Listing(BaseModel):
    """A scouted or fed listing after dedup and Python normalization."""

    key: str = Field(description="Canonical URL; the item's dedup key.")
    kind: ListingKind
    title: str
    company: str
    location_string: str | None
    remote_status_raw: str | None
    url: str
    sources: list[str]
    merged_urls: list[str] = Field(default_factory=list)
    posted_on: date | None
    pay: Pay | None
    # The whole description (a gig's scope), so screening sees requirements after long boilerplate.
    summary: str
    apply_url: str | None = None
    telegram_contact: TelegramContact | None = None
    terms: GigTerms | None = Field(default=None, description="Set for gigs only.")
    eligibility: Literal["yes", "no", "unknown"] = "unknown"
    eligibility_evidence: str | None = None

    def excerpt(self, terms: list[str], chars: int = AGENT_CHARS) -> str:
        """The opening, then each line naming a term: what an agent needs from a long posting."""
        if len(self.summary) <= chars:
            return self.summary
        named = re.compile("|".join(rf"\b{re.escape(t)}\b" for t in terms), re.I) if terms else None
        head, rest = self.summary[:OPENING_CHARS], self.summary[OPENING_CHARS:]
        kept = [head]
        size = len(head)
        for line in rest.splitlines():
            if named is None or not line.strip() or not named.search(line):
                continue
            if size + len(line) + 1 > chars:
                break
            kept.append(line)
            size += len(line) + 1
        return "\n".join(kept) if len(kept) > 1 else self.summary[:chars]

    def for_agents(self, terms: list[str]) -> "Listing":
        return self.model_copy(update={"summary": self.excerpt(terms)})


class Excluded(BaseModel):
    listing: Listing
    reason: ExcludedReason
    detail: str


EmployerSignal = Literal["strong", "established", "small_or_unknown", "thin", "red_flags"]


class ListingJudgment(BaseModel):
    key: str = Field(description="Copy of the listing's `key`.")
    listing_seniority: Seniority | None = Field(
        description="Level the role asks for; null if unclear."
    )
    employer_signal: EmployerSignal
    employer_evidence: str = Field(description="What in the listing supports the signal.")
    deal_breaker_hits: list[str] = Field(description="User deal-breakers the listing mentions.")
    summary: str = Field(description="One sentence on why this role does or doesn't fit.")


class JudgeInput(BaseModel):
    cv: CvProfile
    deal_breakers: list[str]
    listings: list[Listing] = Field(min_length=1, max_length=10)


class JudgeResult(BaseModel):
    judgments: list[ListingJudgment]
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


class ContactInput(BaseModel):
    listing: Listing


class NamedContact(BaseModel):
    name: str = Field(min_length=1)
    title: str | None
    linkedin: str | None
    email: str | None
    source_url: str = Field(description="Page the person was seen on.")


class JobContacts(BaseModel):
    named_contact: NamedContact | None = Field(description="Never invented; null when not found.")
    careers_email: str | None = Field(description="Published on a fetched page.")
    careers_email_source: str | None
    linkedin_company: str | None
    pattern_guesses: list[str] = Field(description="careers@ style guesses; labelled as guesses.")
    apply_url: str | None
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)
