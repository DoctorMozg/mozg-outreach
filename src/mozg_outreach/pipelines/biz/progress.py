"""The user marks approach steps done by hand; each one is logged on the lead once."""

import logging
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from mozg_outreach.pipelines.biz.approach_models import (
    ApproachStep,
    DoneTouch,
    PlannedTouch,
    StoredApproach,
    TouchChannel,
)
from mozg_outreach.pipelines.biz.outreach import APPROACH
from mozg_outreach.storage.models import Interaction, Item, MailThread
from mozg_outreach.storage.repositories import InteractionRepository, ItemRepository

logger = logging.getLogger(__name__)

LOGGED_AS: dict[TouchChannel, str] = {"email": "sent", "linkedin": "sent", "phone": "called"}
CHANNEL_NAMES: dict[TouchChannel, str] = {"email": "email", "linkedin": "LinkedIn", "phone": "call"}


class StepError(ValueError):
    """The step can't change: no plan, or it is already in the state asked for."""


class ApproachProgress:
    def __init__(self, session: Session) -> None:
        self._session = session

    def mark_done(self, item: Item, step: int, on: date) -> ApproachStep:
        approach = _approach(item)
        touch = _touch(approach, step)
        if any(d.step == step for d in approach.done):
            raise StepError(f"step {step} is already done")
        interaction_id = (
            None
            if self._sent_from_gmail(item.id, step)
            else self._log(item.id, approach, touch, on)
        )
        done = [*approach.done, DoneTouch(step=step, on=on, interaction_id=interaction_id)]
        return self._store(item, approach.model_copy(update={"done": done}))

    def undo(self, item: Item, step: int) -> ApproachStep:
        """Takes the step back and deletes the interaction marking it logged."""
        approach = _approach(item)
        entry = next((d for d in approach.done if d.step == step), None)
        if entry is None:
            raise StepError(f"step {step} is not done")
        if entry.interaction_id is not None:
            logged = self._session.get(Interaction, entry.interaction_id)
            if logged is not None:
                self._session.delete(logged)
        done = [d for d in approach.done if d.step != step]
        return self._store(item, approach.model_copy(update={"done": done}))

    def _sent_from_gmail(self, item_id: int, step: int) -> bool:
        """The Gmail poller already logged this step's letter as sent."""
        return (
            self._session.scalar(
                select(MailThread.id).where(
                    MailThread.item_id == item_id,
                    MailThread.step == step,
                    MailThread.sent_at.is_not(None),
                )
            )
            is not None
        )

    def _log(self, item_id: int, approach: StoredApproach, touch: PlannedTouch, on: date) -> int:
        routes = {r.ref: r for c in approach.contacts for r in c.routes}
        channel = routes[touch.route_ref].channel
        note = (
            f"Step {touch.step} ({touch.purpose.replace('_', '-')}): "
            f"{CHANNEL_NAMES[channel]} to {approach.reader(touch)}"
        )
        entry = InteractionRepository(self._session).log(item_id, LOGGED_AS[channel], note)
        if on != entry.happened_at.date():
            entry.happened_at = datetime.combine(on, entry.happened_at.timetz())
        return entry.id

    def _store(self, item: Item, approach: StoredApproach) -> ApproachStep:
        ItemRepository(self._session).set_detail(item, APPROACH, approach)
        current = approach.current()
        logger.info("item %d: approach at step %s of %d", item.id, current.step, current.total)
        return current


def _approach(item: Item) -> StoredApproach:
    if APPROACH not in item.details:
        raise StepError(f"item {item.id} has no approach plan")
    return StoredApproach.model_validate(item.details[APPROACH])


def _touch(approach: StoredApproach, step: int) -> PlannedTouch:
    touch = approach.touch(step)
    if touch is None:
        raise LookupError(f"the approach plan has no step {step}")
    return touch
