"""Fill the accepted report fields per company and judge its fit against the strategy."""

import asyncio
import logging

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.domain import FieldSpec, FieldValue, ItemCandidate
from mozg_outreach.pipelines.biz.assessment_models import (
    AssessmentContext,
    CompanyAssessment,
    FitJudgeInput,
    FitVerdict,
)
from mozg_outreach.pipelines.biz.enrichment import brief_from_candidate
from mozg_outreach.pipelines.biz.enrichment_models import CompanyBrief
from mozg_outreach.pipelines.fields import (
    FieldResearcher,
    FieldResearchInput,
    FieldResearchOutcome,
    FieldSubject,
)
from mozg_outreach.pipelines.steps import PipelineSteps

logger = logging.getLogger(__name__)


class BizAssessment:
    def __init__(self, steps: PipelineSteps) -> None:
        self._steps = steps

    async def research_fields(self, company: CompanyBrief, spec: FieldSpec) -> FieldResearchOutcome:
        subject = FieldSubject.model_validate(company, from_attributes=True)
        return await FieldResearcher(self._steps).research(
            FieldResearchInput(subject=subject, listing=None, fields=spec.fields)
        )

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
