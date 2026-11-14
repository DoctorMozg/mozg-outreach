"""Contracts between the biz discovery pipeline and its agents (inputs and structured outputs)."""

from typing import Literal

from pydantic import BaseModel, Field

from mozg_outreach.domain import FieldSpec

CONCERNS_DESCRIPTION = (
    "Caveats for the user: source conflicts, unverified claims, partial coverage. Empty if none."
)


class Offering(BaseModel):
    what: str = Field(min_length=1)
    value_prop: str = Field(min_length=1)
    differentiators: list[str] = Field(default_factory=list)
    proof_points: list[str] = Field(default_factory=list)


class Icp(BaseModel):
    industries: list[str] = Field(min_length=1)
    company_size: str = ""
    company_stage: str = ""
    buyer_roles: list[str] = Field(default_factory=list)


class Geography(BaseModel):
    target_regions: list[str] = Field(min_length=1)
    notes: str = ""


class GoalAndVoice(BaseModel):
    goal: str = Field(min_length=1)
    channels: Literal["email", "linkedin", "both"] = "both"
    voice: str = ""
    exclusions: list[str] = Field(default_factory=list)


class BizBrief(BaseModel):
    """What the user fills in the brief form; replaces the old intake question loop."""

    offering: Offering
    icp: Icp = Field(title="Ideal customer profile")
    geography: Geography
    goal: GoalAndVoice = Field(title="Goal and voice")
    competitors: list[str] = Field(default_factory=list)
    notes: str = ""


class BriefGap(BaseModel):
    field: str
    question: str


class BriefReview(BaseModel):
    complete: bool
    gaps: list[BriefGap]
    suggestions: list[str]


class FieldProposalInput(BaseModel):
    mode: Literal["biz", "job", "freelance"]
    brief: BizBrief
    current_spec: FieldSpec | None = None


class FieldProposal(BaseModel):
    spec: FieldSpec
    rationale: str


class GoalAnalysis(BaseModel):
    offering: str
    target_audience: str
    timing_rationale: str


class TargetProfile(BaseModel):
    sectors: list[str]
    geography: list[str]
    company_stage: list[str]
    size_range: str
    tech_indicators: list[str]
    maturity_signals: list[str]


class SearchSignals(BaseModel):
    must_have: list[str]
    strong_positive: list[str]
    weak_positive: list[str]
    disqualifying: list[str]


class ScoringWeights(BaseModel):
    data_completeness: int = Field(ge=0, le=100)
    review_reputation: int = Field(ge=0, le=100)
    contact_availability: int = Field(ge=0, le=100)
    growth_signals: int = Field(ge=0, le=100)
    sector_relevance: int = Field(ge=0, le=100)
    outreach_feasibility: int = Field(ge=0, le=100)


class OutreachAngle(BaseModel):
    name: str
    pain_point: str
    value_prop: str
    timing_trigger: str
    personalization_hook: str


ClimateSourceType = Literal[
    "industry_association",
    "chamber_of_commerce",
    "economic_development",
    "government_statistics",
    "regional_press",
    "trade_publication",
    "market_research",
    "regulatory_body",
    "event_organizer",
    "other",
]
SignalType = Literal["economic", "regulatory", "deals", "pain_points"]
OverallClimate = Literal["favorable", "mixed", "challenging", "uncertain"]


class ClimateSourceInput(BaseModel):
    industry: str = Field(min_length=1)
    region: str = Field(min_length=1)


class ClimateSource(BaseModel):
    name: str
    url: str
    type: ClimateSourceType
    signal_types_covered: list[SignalType]
    recency: str
    relevance: int = Field(ge=1, le=10)
    access_notes: str
    search_queries_used: list[str]


class ClimateSources(BaseModel):
    sources: list[ClimateSource]
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


class ClimateResearchInput(BaseModel):
    industry: str = Field(min_length=1)
    region: str = Field(min_length=1)
    sources: list[ClimateSource]
    recency_months: int = Field(ge=1)


class ClimateSignal(BaseModel):
    title: str
    date: str = Field(description="Publication date inside the recency window, YYYY or YYYY-MM.")
    source_url: str
    summary: str
    outreach_implication: str


class ClimateSignals(BaseModel):
    economic: list[ClimateSignal]
    regulatory: list[ClimateSignal]
    deals: list[ClimateSignal]
    pain_points: list[ClimateSignal]


class ClimateAngle(BaseModel):
    angle: str
    based_on: list[str] = Field(
        min_length=1, description="Titles of the signals this angle rests on."
    )
    why_it_lands: str


class ClimateReport(BaseModel):
    industry: str
    region: str
    recency_months: int
    signals: ClimateSignals
    top_outreach_angles: list[ClimateAngle]
    climate_summary: str
    overall_climate: OverallClimate
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


class StrategyInput(BaseModel):
    goal: str
    sector: str | None = None
    region: str | None = Field(
        default=None, description="Hard geographic filter; overrides geography in goal and brief."
    )
    brief: BizBrief | None = None
    climate: ClimateReport | None = None
    feedback: str | None = Field(default=None, description="The user's notes on a rejected draft.")


class Strategy(BaseModel):
    goal_analysis: GoalAnalysis
    target_profile: TargetProfile
    search_signals: SearchSignals
    scoring_weights: ScoringWeights
    scoring_rationale: str
    outreach_angles: list[OutreachAngle]
    sender_voice: str
    source_hints: list[str]
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


SourceType = Literal[
    "startup_directory",
    "industry_association",
    "government_registry",
    "aggregator",
    "chamber_of_commerce",
    "vc_portfolio",
    "event_list",
    "other",
]


class SourceResearchInput(BaseModel):
    target_description: str
    source_hints: list[str]


class DirectorySource(BaseModel):
    name: str
    url: str
    type: SourceType
    estimated_count: int | None
    relevance: int = Field(ge=1, le=10)
    access_notes: str
    search_queries_used: list[str]


class SourceList(BaseModel):
    sources: list[DirectorySource]
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


class ScoutInput(BaseModel):
    source: DirectorySource
    target_profile: TargetProfile
    sector: str | None = None
    region: str | None = Field(
        default=None, description="Hard filter: keep only companies located inside it."
    )
    count_limit: int = Field(ge=1)


class ScoutedCompany(BaseModel):
    name: str
    domain: str | None
    sector: str | None
    location: str | None
    founded: str | None
    description: str | None
    source_url: str = Field(description="Page where this company was seen.")
    evidence_quote: str | None = Field(
        description="Verbatim text from source_url that mentions the company; null if none."
    )
    evidence: Literal["page", "search_snippet"] = Field(
        description="page: the quote was read on the fetched source_url. "
        "search_snippet: it came from a search result for that URL."
    )


class ScoutResult(BaseModel):
    companies: list[ScoutedCompany]
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)
