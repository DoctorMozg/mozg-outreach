"""Addresses no letter goes to again, in any project: bounced, or the reader asked to stop."""

import logging

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from mozg_outreach.storage.models import Draft, MailThread, Suppression

logger = logging.getLogger(__name__)

BOUNCED = "bounced"
EMAIL = "email"


def domain_of(address: str) -> str:
    return address.rpartition("@")[2].lower()


class Blocklist(BaseModel):
    addresses: dict[str, str] = Field(default_factory=dict, description="Address to reason.")
    bounced_domains: set[str] = Field(default_factory=set)

    def blocks(self, address: str, guessed: bool) -> str | None:
        """Why no letter may go to `address`, or None when it may."""
        address = address.lower()
        if address in self.addresses:
            return self.addresses[address]
        # One bounce on a domain means its address pattern was wrong, so every guess there is too.
        if guessed and domain_of(address) in self.bounced_domains:
            return f"another address at {domain_of(address)} bounced, so the guess is unsafe"
        return None


class Suppressions:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, address: str, reason: str, item_id: int | None) -> bool:
        """Suppresses one address; False when it already was."""
        address = address.lower()
        if self._session.scalar(select(Suppression.id).where(Suppression.address == address)):
            return False
        self._session.add(
            Suppression(address=address, domain=domain_of(address), reason=reason, item_id=item_id)
        )
        self._session.flush()
        logger.info("suppressed %s: %s", address, reason)
        return True

    def add_lead(self, item_id: int, reason: str) -> list[str]:
        """Suppresses every address the lead's email letters were written for or sent to."""
        found = {
            str(d.review["address"]).lower()
            for d in self._session.scalars(
                select(Draft).where(Draft.item_id == item_id, Draft.channel == EMAIL)
            )
            if "@" in str(d.review.get("address", ""))
        }
        found |= {
            a.lower()
            for a in self._session.scalars(
                select(MailThread.address).where(MailThread.item_id == item_id)
            )
        }
        return [a for a in sorted(found) if self.add(a, reason, item_id)]

    def lift(self, item_id: int, reason: str) -> int:
        """Undoes `add_lead` when the user deletes the interaction that caused it."""
        rows = self._session.scalars(
            select(Suppression).where(Suppression.item_id == item_id, Suppression.reason == reason)
        ).all()
        for row in rows:
            self._session.delete(row)
        if rows:
            logger.info("lifted %d suppression(s) of item %d (%s)", len(rows), item_id, reason)
        return len(rows)

    def blocklist(self) -> Blocklist:
        rows = self._session.scalars(select(Suppression)).all()
        return Blocklist(
            addresses={r.address: r.reason for r in rows},
            bounced_domains={r.domain for r in rows if r.reason == BOUNCED},
        )
