"""After a search, starts enrichment for its best new leads, as the user's settings allow."""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from mozg_outreach.domain import ItemKind
from mozg_outreach.runs.flow import FlowContext, RunState
from mozg_outreach.settings.store import SettingsStore
from mozg_outreach.storage.models import Item, Run

logger = logging.getLogger(__name__)

# Gigs are answered on their own board, so they have nothing to enrich.
ENRICH_PIPELINES: dict[ItemKind, str] = {
    ItemKind.COMPANY: "biz.enrich",
    ItemKind.JOB: "seeker.enrich",
}


def start_enrichments[TState: RunState](ctx: FlowContext[TState], item_ids: list[int]) -> list[int]:
    """Starts one enrichment run per qualifying lead and returns their run ids."""
    if ctx.start_run is None:
        raise RuntimeError("this run can't start other runs")
    with ctx.db.session() as session:
        minimum = SettingsStore(session).load().auto_enrich_min_score
        if minimum is None:
            return []
        busy = _being_enriched(session, ctx.project_id)
        due = [
            (item.id, ENRICH_PIPELINES[item.kind])
            for item in (session.get(Item, i) for i in item_ids)
            if item is not None
            and item.kind in ENRICH_PIPELINES
            and item.archived_at is None
            and not item.enriched
            and (item.score_total or 0) >= minimum
            and item.id not in busy
        ]
    started = [ctx.start_run(pipeline, {"item_id": item_id}) for item_id, pipeline in due]
    logger.info("auto-enrichment at score %d started %d runs", minimum, len(started))
    ctx.emit("auto_enrich", {"min_score": minimum, "items": [i for i, _ in due], "runs": started})
    return started


def _being_enriched(session: Session, project_id: int) -> set[int]:
    """Items an unfinished enrichment run is already working on."""
    runs = session.scalars(
        select(Run).where(
            Run.project_id == project_id,
            Run.pipeline.in_(ENRICH_PIPELINES.values()),
            Run.finished_at.is_(None),
        )
    )
    return {i for run in runs if isinstance(i := run.state.get("item_id"), int)}
