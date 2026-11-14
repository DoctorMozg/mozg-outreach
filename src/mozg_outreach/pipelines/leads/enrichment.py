"""Identify every scored job or gig lead, then register what was confirmed."""

import logging

from pydantic import BaseModel

from mozg_outreach.pipelines.job.models import Listing
from mozg_outreach.pipelines.leads.identify import LeadIdentifier
from mozg_outreach.pipelines.leads.ingest import LeadRegistryIngest
from mozg_outreach.pipelines.leads.models import LeadInput
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.registry.ingest import IngestContext
from mozg_outreach.storage.db import Database
from mozg_outreach.storage.models import Item
from mozg_outreach.storage.repositories import ItemRepository

logger = logging.getLogger(__name__)

IDENTITY = "identity"


class LeadTask(BaseModel):
    item_id: int
    listing: Listing
    lead: LeadInput


class RunRef(BaseModel):
    project_id: int
    run_id: int | None


class EnrichReport(BaseModel):
    companies: int
    people: int
    concerns: list[str]


class LeadEnrichment:
    def __init__(self, db: Database, steps: PipelineSteps) -> None:
        self._db = db
        self._identifier = LeadIdentifier(steps)

    async def run(self, tasks: list[LeadTask], ref: RunRef) -> EnrichReport:
        outcomes = await self._identifier.identify_many([t.lead for t in tasks])
        report = EnrichReport(companies=0, people=0, concerns=[])
        with self._db.session() as session:
            items, ingest = ItemRepository(session), LeadRegistryIngest(session)
            for task, outcome in zip(tasks, outcomes, strict=True):
                report.concerns += outcome.concerns
                item = session.get(Item, task.item_id)
                if item is None:
                    continue  # deleted mid-run
                context = IngestContext(
                    project_id=ref.project_id, run_id=ref.run_id, item_id=task.item_id
                )
                # The post names its contact itself, so it counts even when identification fails.
                if ingest.telegram_contact(task.listing, context) is not None:
                    report.people += 1
                if outcome.identity is None:
                    continue
                items.set_detail(item, IDENTITY, outcome.identity)
                counts = ingest.identity(task.listing, outcome.identity, context)
                report.companies += counts.companies
                report.people += counts.people
        logger.info(
            "leads: %d of %d with a company, %d people", report.companies, len(tasks), report.people
        )
        return report
