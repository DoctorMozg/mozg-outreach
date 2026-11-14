"""Contracts for filling the user's report fields and judging fit against the strategy."""

from typing import Literal

from pydantic import BaseModel, Field

from mozg_outreach.domain import FieldDefinition, FieldScalar, FieldSpec, FieldValue
from mozg_outreach.pipelines.biz.enrichment_models import CompanyBrief, CompanyDossier
from mozg_outreach.pipelines.biz.models import CONCERNS_DESCRIPTION, SearchSignals, Strategy


class FieldResearchInput(BaseModel):
    company: CompanyBrief
    fields: list[FieldDefinition] = Field(min_length=1)


class ResearchedValue(BaseModel):
    field_id: str
    value: FieldScalar = Field(description="Typed per the field's `type`; null when not found.")
    source_url: str | None = Field(description="Page the value was read from.")
    quote: str | None = Field(description="Verbatim text from source_url that supports the value.")


class FieldResearchResult(BaseModel):
    values: list[ResearchedValue]
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


SignalKind = Literal["must_have", "strong_positive", "weak_positive", "disqualifying"]


class FitJudgeInput(BaseModel):
    company: CompanyBrief
    facts: dict[str, FieldValue] = Field(description="Scouted and researched fields by id.")
    dossier: CompanyDossier | None = Field(
        description="Enrichment results; null before enrichment."
    )
    search_signals: SearchSignals


class SignalCheck(BaseModel):
    signal: str = Field(description="The signal text exactly as given in search_signals.")
    kind: SignalKind
    present: Literal["yes", "no", "unknown"]
    evidence: str | None = Field(description="Dossier fact or quote behind the answer.")
    source_url: str | None


class FitVerdict(BaseModel):
    sector_match: Literal["exact", "adjacent", "unrelated"]
    sector_reason: str
    signals: list[SignalCheck]
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


class AssessmentContext(BaseModel):
    strategy: Strategy
    spec: FieldSpec


class CompanyAssessment(BaseModel):
    """Run-time assessment of one company, before any enrichment."""

    fields: dict[str, FieldValue] = Field(description="Accepted researched values by field id.")
    verdict: FitVerdict | None = Field(description="None when the fit judge failed.")
    concerns: list[str]
