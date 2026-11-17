"""Contracts for one more round of searching on a registry person or company."""

from typing import Literal

from pydantic import BaseModel, Field

from mozg_outreach.pipelines.biz.models import CONCERNS_DESCRIPTION
from mozg_outreach.pipelines.leads.models import IdentifiedCompany, NamedPerson, SourcedFact


class EntityResearchInput(BaseModel):
    kind: Literal["person", "company"]
    name: str
    known: dict[str, str] = Field(description="What the registry holds now, by field name.")
    employers: list[str] = Field(description="For a person: the companies they're linked to.")
    people: list[str] = Field(description="For a company: its linked people, 'Name, title'.")
    sources: list[str] = Field(description="Pages the registry saw it on, newest first.")
    rejected: list[str] = Field(description="Identifiers shown to be someone else's.")
    note: str | None = Field(description="The user's hint for this round; null when none.")


class FoundPerson(NamedPerson):
    location: SourcedFact | None = Field(default=None, description="Where they're based.")


class IdentifierCheck(BaseModel):
    kind: Literal["linkedin", "domain"]
    value: str = Field(description="The known identifier, copied from `known`.")
    verdict: Literal["matches", "someone_else", "unclear"]
    source_url: str | None = Field(description="The page the verdict rests on; null if unclear.")
    quote: str = Field(description="Verbatim text from it that shows the verdict.")


class EntityFindings(BaseModel):
    company: IdentifiedCompany | None = Field(
        description="The company itself, or the person's current employer; null if unconfirmed."
    )
    people: list[FoundPerson] = Field(
        description="For a person: them, first. For a company: its key people."
    )
    checks: list[IdentifierCheck] = Field(description="One per known LinkedIn and domain.")
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)
