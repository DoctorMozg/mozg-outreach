"""Fill the accepted report fields per company and judge its fit against the strategy."""

import asyncio
import logging

from pydantic import BaseModel

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.domain import (
    FieldDefinition,
    FieldScalar,
    FieldSpec,
    FieldType,
    FieldValue,
    ItemCandidate,
)
from mozg_outreach.pipelines.biz.assessment_models import (
    AssessmentContext,
    CompanyAssessment,
    FieldResearchInput,
    FieldResearchResult,
    FitJudgeInput,
    FitVerdict,
)
from mozg_outreach.pipelines.biz.enrichment import brief_from_candidate
from mozg_outreach.pipelines.biz.enrichment_models import CompanyBrief
from mozg_outreach.pipelines.steps import PipelineSteps

logger = logging.getLogger(__name__)


class FieldResearchOutcome(BaseModel):
    values: dict[str, FieldValue]
    concerns: list[str]


class BizAssessment:
    def __init__(self, steps: PipelineSteps) -> None:
        self._steps = steps

    async def research_fields(self, company: CompanyBrief, spec: FieldSpec) -> FieldResearchOutcome:
        payload = FieldResearchInput(company=company, fields=spec.fields)
        result = await self._steps.run("shared", "field-researcher", payload, FieldResearchResult)
        return accept_field_values(result, spec)

    async def judge_fit(self, payload: FitJudgeInput) -> FitVerdict:
        return await self._steps.run("biz", "fit-judge", payload, FitVerdict)

    async def assess(
        self, candidate: ItemCandidate, context: AssessmentContext
    ) -> CompanyAssessment:
        """Research the report fields, then judge fit on scouted plus researched facts."""
        company = brief_from_candidate(candidate, context.strategy)
        fields: dict[str, FieldValue] = {}
        concerns: list[str] = []
        try:
            research = await self.research_fields(company, context.spec)
            fields, concerns = research.values, research.concerns
        except (AgentRunError, AgentOutputError) as exc:
            # Fit can still be judged from the scouted fields alone.
            logger.warning("field research for %s failed: %s", company.name, exc)
            concerns.append(f"Field research for {company.name} failed: {exc}")
        verdict: FitVerdict | None = None
        try:
            verdict = await self.judge_fit(
                FitJudgeInput(
                    company=company,
                    facts={**candidate.fields, **fields},
                    dossier=None,
                    search_signals=context.strategy.search_signals,
                )
            )
        except (AgentRunError, AgentOutputError) as exc:
            logger.warning("fit judge for %s failed: %s", company.name, exc)
            concerns.append(f"Fit judge for {company.name} failed: {exc}")
        return CompanyAssessment(fields=fields, verdict=verdict, concerns=concerns)

    async def assess_many(
        self, candidates: list[ItemCandidate], context: AssessmentContext
    ) -> list[CompanyAssessment]:
        return list(await asyncio.gather(*(self.assess(c, context) for c in candidates)))


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
