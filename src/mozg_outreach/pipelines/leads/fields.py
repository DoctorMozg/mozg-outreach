"""Fill the project's report fields for the best job or gig leads."""

import asyncio
import logging

from pydantic import BaseModel

from mozg_outreach.agents import AgentOutputError, AgentRunError
from mozg_outreach.domain import FieldSpec
from mozg_outreach.pipelines.fields import (
    FieldResearcher,
    FieldResearchInput,
    FieldResearchOutcome,
    FieldSubject,
    ListingContext,
)
from mozg_outreach.pipelines.leads.models import LeadIdentity, LeadInput
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.storage.db import Database
from mozg_outreach.storage.models import Item
from mozg_outreach.storage.repositories import ItemRepository

logger = logging.getLogger(__name__)


class FieldTask(BaseModel):
    item_id: int
    request: FieldResearchInput


def field_task(
    item_id: int, lead: LeadInput, identity: LeadIdentity | None, spec: FieldSpec
) -> FieldTask:
    """The confirmed company's facts when basic enrichment found them, else the listed name."""
    company = identity.company if identity else None
    subject = FieldSubject(name=lead.organization or None)
    if company is not None:
        subject = FieldSubject(
            name=company.name,
            domain=company.domain.value if company.domain else None,
            location=company.location.value if company.location else None,
            sector=company.sector.value if company.sector else None,
            description=company.description.value if company.description else None,
        )
    context = ListingContext(
        title=lead.title, url=lead.url, apply_url=lead.apply_url, summary=lead.summary
    )
    return FieldTask(
        item_id=item_id,
        request=FieldResearchInput(subject=subject, listing=context, fields=spec.fields),
    )


class LeadFieldResearch:
    def __init__(self, db: Database, steps: PipelineSteps) -> None:
        self._db = db
        self._researcher = FieldResearcher(steps)

    async def run(self, tasks: list[FieldTask]) -> list[str]:
        """Research every task, merge accepted values into its item and return the concerns."""
        outcomes = await asyncio.gather(*(self._research(t) for t in tasks))
        concerns: list[str] = []
        with self._db.session() as session:
            items = ItemRepository(session)
            for task, outcome in zip(tasks, outcomes, strict=True):
                title = task.request.listing.title if task.request.listing else task.item_id
                concerns += [f"{title}: {c}" for c in outcome.concerns]
                item = session.get(Item, task.item_id)
                if item is not None and outcome.values:
                    items.merge_fields(item, outcome.values)
        return concerns

    async def _research(self, task: FieldTask) -> FieldResearchOutcome:
        try:
            return await self._researcher.research(task.request)
        except (AgentRunError, AgentOutputError) as exc:
            # The lead keeps its fixed columns; only the custom ones stay empty.
            logger.warning("field research for item %d failed: %s", task.item_id, exc)
            return FieldResearchOutcome(values={}, concerns=[f"field research failed: {exc}"])
