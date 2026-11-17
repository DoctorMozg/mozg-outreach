"""Contracts between the biz enrichment pipeline and its five per-company agents."""

from typing import Literal

from pydantic import BaseModel, Field

from mozg_outreach.pipelines.biz.models import CONCERNS_DESCRIPTION, OutreachAngle, TargetProfile

Sentiment = Literal["positive", "mixed", "negative", "no_data"]
Confidence = Literal["low", "medium", "high"]


class CompanyBrief(BaseModel):
    """Shared input of all enrichment agents; the strategy fields matter mostly to contacts."""

    name: str = Field(min_length=1)
    domain: str | None = None
    location: str | None = None
    sector: str | None = None
    description: str | None = None
    decision_maker: str | None = Field(default=None, description="Who search named, with role.")
    preferred_channel: str | None = None
    recent_trigger: str | None = None
    source_urls: list[str] = Field(default_factory=list, description="Pages search found it on.")
    target_profile: TargetProfile | None = None
    outreach_angles: list[OutreachAngle] = Field(default_factory=list)


class PlatformReview(BaseModel):
    rating: float | None = Field(ge=0, le=5)
    count: int = Field(ge=0)
    sentiment: Sentiment
    notable: str | None
    source_url: str | None


class PlatformReviews(BaseModel):
    glassdoor: PlatformReview
    trustpilot: PlatformReview
    indeed: PlatformReview
    google_business: PlatformReview


class ReputationResult(BaseModel):
    reviews: PlatformReviews
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


class EmailContact(BaseModel):
    address: str = Field(min_length=3)
    kind: Literal["published", "pattern_guess"]
    source_url: str = Field(description="Page the address, or the pattern behind it, was seen on.")


class ProfileLink(BaseModel):
    url: str = Field(description="A LinkedIn profile the company's own site links to.")
    text: str = Field(description="The link's text, often the person's name.")
    source_url: str


class SiteContacts(BaseModel):
    """What the company's own pages publish, read by Python, not by a model."""

    emails: list[EmailContact] = Field(default_factory=list)
    profiles: list[ProfileLink] = Field(default_factory=list)
    pages: list[str] = Field(default_factory=list, description="Pages read.")
    contact_form: str | None = Field(default=None, description="Page with a contact form.")
    concerns: list[str] = Field(default_factory=list)


class ContactBrief(CompanyBrief):
    """The contact finder's input: the company plus what its own pages already publish."""

    site: SiteContacts | None = Field(
        default=None, description="Emails and profile links Python read on the company's site."
    )


class PhoneContact(BaseModel):
    number: str = Field(min_length=3)
    source_url: str


class CompanyWebsite(BaseModel):
    url: str = Field(description="The company's own website.")
    source_url: str = Field(description="Page that shows this is the company's website.")
    quote: str


class SocialLinks(BaseModel):
    linkedin_company: str | None
    twitter: str | None


class KeyPerson(BaseModel):
    name: str = Field(min_length=1)
    title: str
    linkedin: str | None
    email: EmailContact | None
    phone: PhoneContact | None = None
    relevance: str
    source_url: str = Field(description="Page where this person was seen.")
    quote: str = Field(default="", description="Exact sentence on that page naming the person.")
    confidence: Confidence = "low"


class ContactsResult(BaseModel):
    website: CompanyWebsite | None = None
    emails: list[EmailContact]
    phones: list[PhoneContact]
    whatsapp: str | None
    booking_link: str | None
    contact_form: str | None = Field(
        default=None, description="Page of the company's website contact form."
    )
    address: str | None
    social: SocialLinks
    key_people: list[KeyPerson]
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


class NewsItem(BaseModel):
    title: str
    date: str = Field(description="Publication date, YYYY-MM-DD or YYYY-MM.")
    url: str
    summary: str
    category: Literal["funding", "partnership", "product", "expansion", "people", "award", "other"]
    timing_relevance: Literal["high", "medium", "low"]
    outreach_implication: str


class NewsResult(BaseModel):
    items: list[NewsItem]
    funding_status: str | None
    latest_activity: str | None
    overall_momentum: Literal["strong", "steady", "quiet", "concerning"]
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


class JobPostings(BaseModel):
    total_open: int = Field(ge=0)
    departments: dict[str, int]
    seniority_mix: str
    notable_roles: list[str]


class CompanySize(BaseModel):
    estimate: Literal["1-10", "10-50", "50-200", "200-500", "500-1000", "1000+"]
    confidence: Confidence
    signals: str


class FundingSignals(BaseModel):
    last_round: str | None
    total_raised: str | None
    key_investors: list[str]
    date: str | None


class GrowthResult(BaseModel):
    job_postings: JobPostings
    company_size: CompanySize
    funding: FundingSignals | None
    trajectory: Literal["rapid_growth", "steady_growth", "stable", "declining", "pivoting"]
    trajectory_evidence: str
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


class TechStack(BaseModel):
    languages: list[str]
    frameworks: list[str]
    cloud: list[str]
    databases: list[str]
    devops: list[str]
    monitoring: list[str]
    other: list[str]


class TechDecisionMaker(BaseModel):
    name: str = Field(min_length=1)
    title: str
    likely_priorities: str
    source_url: str = Field(description="Page where this person was seen.")
    quote: str = Field(default="", description="Exact sentence on that page naming the person.")
    confidence: Confidence = "low"


class TechResult(BaseModel):
    stack: TechStack
    stack_confidence: Confidence
    stack_sources: list[str]
    github_org: str | None
    tech_blog: str | None
    open_source: str | None
    conference_presence: str | None
    maturity: Literal["early", "mid", "mature", "enterprise"]
    maturity_notes: str
    tech_decision_makers: list[TechDecisionMaker]
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


class CompanyDossier(BaseModel):
    """Enrichment outcome for one company; a section is None when its agent failed."""

    reputation: ReputationResult | None = None
    contacts: ContactsResult | None = None
    news: NewsResult | None = None
    growth: GrowthResult | None = None
    tech: TechResult | None = None
    concerns: list[str] = Field(default_factory=list)
