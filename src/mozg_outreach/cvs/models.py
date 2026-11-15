"""Contracts of the CV profiler and picker, and the pick stored on a lead."""

from typing import Literal

from pydantic import BaseModel, Field

from mozg_outreach.pipelines.biz.models import CONCERNS_DESCRIPTION
from mozg_outreach.pipelines.seeker.models import Listing, Seniority

# `Item.details` key of the CV picked for a lead.
CV_CHOICE = "cv_choice"
# Character budget per CV in the picker's input, enough for the summary and recent roles.
PICK_CHARS = 3000
PROFILE_CHARS = 12000


class CvSummary(BaseModel):
    """What a CV is for, so the picker and the user can tell several CVs apart."""

    language: str = Field(min_length=2, max_length=2, description="ISO 639-1 code of the CV text.")
    headline: str = Field(min_length=1, description="The role the CV presents, in a few words.")
    seniority: Seniority
    titles: list[str] = Field(min_length=1, description="Job titles the CV targets or held.")
    domains: list[str] = Field(description="Industries or problem areas it shows, e.g. payments.")
    core_skills: list[str] = Field(description="The skills it puts first, at most ten.")


class CvProfilerInput(BaseModel):
    cv_text: str


class CvProfileDraft(BaseModel):
    summary: CvSummary
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


class CvOption(BaseModel):
    id: int
    name: str
    summary: CvSummary
    excerpt: str = Field(description="The CV text, cut to its opening.")


class CvPickInput(BaseModel):
    listing: Listing
    cvs: list[CvOption] = Field(min_length=2)


class CvRating(BaseModel):
    """How well one CV answers the listing, per axis from 0 (not at all) to 4 (fully)."""

    cv_id: int = Field(description="Copy of the CV's `id`.")
    skills: int = Field(ge=0, le=4)
    title: int = Field(ge=0, le=4)
    seniority: int = Field(ge=0, le=4)
    domain: int = Field(ge=0, le=4)
    reason: str = Field(description="One sentence naming what decides the ratings.")


class CvRatings(BaseModel):
    listing_language: str = Field(
        min_length=2, max_length=2, description="ISO 639-1 code of the listing text."
    )
    ratings: list[CvRating]
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


class RankedCv(BaseModel):
    cv_id: int
    name: str
    total: float | None = Field(description="0 to 100 from the ratings; null when not rated.")
    in_listing_language: bool
    reason: str | None


class CvChoice(BaseModel):
    """The CV to send for one lead: picked by the ratings, the only CV, or by the user."""

    cv_id: int
    picked_by: Literal["ratings", "only", "user"]
    listing_language: str | None
    ranked: list[RankedCv] = Field(description="Best first; empty when nothing was rated.")
    concerns: list[str]
