"""Reply rates per angle and per touch purpose, with honest uncertainty at small counts."""

import logging
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from math import comb
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from mozg_outreach.pipelines.biz.approach_models import StoredApproach
from mozg_outreach.pipelines.biz.outreach import APPROACH
from mozg_outreach.storage.models import Draft, Item, MailThread

logger = logging.getLogger(__name__)

LEVEL = 0.9
OutcomeGroup = Literal["angle", "purpose"]


class Rate(BaseModel):
    """A Beta(1, 1) posterior on the reply rate: mean and a central interval."""

    sent: int
    replied: int
    mean: float
    low: float
    high: float

    @classmethod
    def of(cls, replied: int, sent: int, level: float = LEVEL) -> "Rate":
        if not 0 <= replied <= sent:
            raise ValueError(f"replied {replied} must be between 0 and sent {sent}")
        a, b = 1 + replied, 1 + sent - replied
        tail = (1 - level) / 2
        return cls(
            sent=sent,
            replied=replied,
            mean=round(a / (a + b), 3),
            low=round(_beta_quantile(tail, a, b), 3),
            high=round(_beta_quantile(1 - tail, a, b), 3),
        )


class OutcomeRow(BaseModel):
    group: OutcomeGroup
    name: str
    rate: Rate


class Outcomes(BaseModel):
    rows: list[OutcomeRow]
    level: float = Field(default=LEVEL, description="The interval's probability mass.")
    sent: int = Field(description="Letters sent from Gmail in this project.")


class _Sent(BaseModel):
    angle: str
    purpose: str
    replied: bool


def project_outcomes(session: Session, project_id: int) -> Outcomes:
    """Counts the letters the Gmail poller saw sent; a reply goes to the newest one before it."""
    rows = session.execute(
        select(MailThread, Item, Draft)
        .join(Item, Item.id == MailThread.item_id)
        .join(Draft, Draft.id == MailThread.draft_id)
        .where(Item.project_id == project_id, MailThread.sent_at.is_not(None))
    ).all()
    credited = _credited([thread for thread, _, _ in rows])
    sent = [
        _Sent(**_labels(item, draft, thread.step), replied=thread.id in credited)
        for thread, item, draft in rows
    ]
    found: list[OutcomeRow] = []
    for group in ("angle", "purpose"):
        counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for letter in sent:
            count = counts[getattr(letter, group)]
            count[0] += 1
            count[1] += letter.replied
        found += [
            OutcomeRow(group=group, name=name, rate=Rate.of(replied, total))
            for name, (total, replied) in sorted(counts.items())
        ]
    logger.info("outcomes for project %d: %d sent letters", project_id, len(sent))
    return Outcomes(rows=found, sent=len(sent))


def _credited(threads: Sequence[MailThread]) -> set[int]:
    """Steps sharing a Gmail thread all see its reply; it answers the newest one sent before it."""
    by_thread: dict[int, list[MailThread]] = defaultdict(list)
    for row in threads:
        by_thread[row.gmail_thread_id].append(row)
    credited: set[int] = set()
    for rows in by_thread.values():
        replies = [r.replied_at for r in rows if r.replied_at is not None]
        if not replies:
            continue
        first = min(replies)
        before = [r for r in rows if r.sent_at is not None and _naive(r.sent_at) <= _naive(first)]
        if before:
            credited.add(max(before, key=lambda r: _naive(r.sent_at or first)).id)
    return credited


def _labels(item: Item, draft: Draft, step: int | None) -> dict[str, str]:
    """The plan's angle and purpose for a planned step, else what the draft says it used."""
    raw = item.details.get(APPROACH)
    touch = StoredApproach.model_validate(raw).touch(step) if raw and step else None
    if touch is not None:
        return {"angle": touch.angle, "purpose": touch.purpose}
    return {"angle": str(draft.review.get("angle_used") or "unknown"), "purpose": "open"}


def _naive(moment: datetime) -> datetime:
    # SQLite hands back naive datetimes; ours are UTC.
    return moment.replace(tzinfo=None)


def _beta_cdf(x: float, a: int, b: int) -> float:
    """For whole a and b, Beta(a, b) up to x is P(Binomial(a + b - 1, x) >= a)."""
    n = a + b - 1
    return sum(comb(n, k) * x**k * (1 - x) ** (n - k) for k in range(a, n + 1))


def _beta_quantile(p: float, a: int, b: int) -> float:
    low, high = 0.0, 1.0
    for _ in range(60):
        mid = (low + high) / 2
        if _beta_cdf(mid, a, b) < p:
            low = mid
        else:
            high = mid
    return (low + high) / 2
