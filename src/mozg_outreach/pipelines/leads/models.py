"""Contracts for the basic enrichment of job listings and freelance gigs."""

from typing import Literal

from pydantic import BaseModel, Field

from mozg_outreach.pipelines.biz.models import CONCERNS_DESCRIPTION

LeadMode = Literal["job", "freelance"]


class LeadInput(BaseModel):
    mode: LeadMode
    title: str
    organization: str | None = Field(description="As the listing shows it; null when hidden.")
    url: str
    location: str | None
    summary: str


class SourcedFact(BaseModel):
    value: str = Field(min_length=1)
    source_url: str = Field(min_length=8)
    quote: str = Field(description="Verbatim text from the page that shows the value.")


class IdentifiedCompany(BaseModel):
    name: str = Field(min_length=1)
    source_url: str = Field(min_length=8, description="Page that ties the company to the listing.")
    quote: str
    domain: SourcedFact | None
    linkedin: SourcedFact | None
    sector: SourcedFact | None
    location: SourcedFact | None
    description: SourcedFact | None


class NamedPerson(BaseModel):
    name: str = Field(min_length=1)
    title: str | None
    role: Literal["hiring_manager", "recruiter", "client_contact", "other"]
    linkedin: str | None
    email: str | None = Field(description="Only when shown on the page, inside `quote`.")
    source_url: str = Field(min_length=8)
    quote: str = Field(description="Verbatim text from the page that contains the name.")


class LeadIdentity(BaseModel):
    company: IdentifiedCompany | None = Field(description="Null when it can't be confirmed.")
    people: list[NamedPerson]
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)
