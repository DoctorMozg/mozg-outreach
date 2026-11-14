"""Report fields for every mode: the proposer drafts a spec, the researcher fills it per lead."""

import logging

from pydantic import BaseModel, Field

from mozg_outreach.domain import (
    FieldDefinition,
    FieldScalar,
    FieldSpec,
    FieldType,
    FieldValue,
    ProjectMode,
)
from mozg_outreach.pipelines.biz.models import CONCERNS_DESCRIPTION, BizBrief
from mozg_outreach.pipelines.steps import PipelineSteps

logger = logging.getLogger(__name__)


class SeekerBrief(BaseModel):
    """A job or freelance brief without the CV, which proposing fields doesn't need."""

    preferences: str


class FieldProposalInput(BaseModel):
    mode: ProjectMode
    brief: BizBrief | SeekerBrief
    current_spec: FieldSpec | None = None


class FieldProposal(BaseModel):
    spec: FieldSpec
    rationale: str


class FieldSubject(BaseModel):
    """The company the fields are about; a gig whose board hides the client has no name."""

    name: str | None = Field(min_length=1)
    domain: str | None = None
    location: str | None = None
    sector: str | None = None
    description: str | None = None


class ListingContext(BaseModel):
    title: str
    url: str
    apply_url: str | None = Field(default=None, description="Vacancy page the listing links to.")
    summary: str


class FieldResearchInput(BaseModel):
    subject: FieldSubject
    listing: ListingContext | None = Field(description="The job or gig; null for biz companies.")
    fields: list[FieldDefinition] = Field(min_length=1)


class ResearchedValue(BaseModel):
    field_id: str
    value: FieldScalar = Field(description="Typed per the field's `type`; null when not found.")
    source_url: str | None = Field(description="Page the value was read from.")
    quote: str | None = Field(description="Verbatim text from source_url that supports the value.")


class FieldResearchResult(BaseModel):
    values: list[ResearchedValue]
    concerns: list[str] = Field(description=CONCERNS_DESCRIPTION)


class FieldResearchOutcome(BaseModel):
    values: dict[str, FieldValue]
    concerns: list[str]


class FieldProposer:
    def __init__(self, steps: PipelineSteps) -> None:
        self._steps = steps

    async def propose(self, request: FieldProposalInput) -> FieldProposal:
        return await self._steps.run("shared", "field-proposer", request, FieldProposal)


class FieldResearcher:
    def __init__(self, steps: PipelineSteps) -> None:
        self._steps = steps

    async def research(self, request: FieldResearchInput) -> FieldResearchOutcome:
        result = await self._steps.run("shared", "field-researcher", request, FieldResearchResult)
        return accept_field_values(result, FieldSpec(fields=request.fields))


def accept_field_values(result: FieldResearchResult, spec: FieldSpec) -> FieldResearchOutcome:
    """Keep values for known fields that match their type and cite a source page."""
    definitions = spec.by_id()
    values: dict[str, FieldValue] = {}
    concerns = list(result.concerns)
    for researched in result.values:
        definition = definitions.get(researched.field_id)
        if definition is None:
            concerns.append(f"Dropped value for unknown field '{researched.field_id}'.")
        elif researched.value is None:
            continue
        elif not matches_type(researched.value, definition):
            concerns.append(
                f"Dropped '{definition.id}': {researched.value!r} is not a {definition.type}."
            )
        elif not researched.source_url:
            concerns.append(f"Dropped '{definition.id}': no source page cited.")
        else:
            values[definition.id] = FieldValue(
                value=researched.value, source_url=researched.source_url, quote=researched.quote
            )
    if len(concerns) > len(result.concerns):
        logger.info("field research: %d values dropped", len(concerns) - len(result.concerns))
    return FieldResearchOutcome(values=values, concerns=concerns)


def matches_type(value: FieldScalar, definition: FieldDefinition) -> bool:
    match definition.type:
        case FieldType.NUMBER:
            return isinstance(value, int | float) and not isinstance(value, bool)
        case FieldType.BOOLEAN:
            return isinstance(value, bool)
        case FieldType.LIST:
            return isinstance(value, list)
        case _:
            return isinstance(value, str) and bool(value.strip())
