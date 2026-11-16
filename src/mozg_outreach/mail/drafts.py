"""Email letters in the user's Gmail: pushed as drafts, then marked sent, replied or bounced."""

import asyncio
import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from mozg_outreach.mail.gmail import Mailbox
from mozg_outreach.mail.models import MailError, OutgoingDraft, ThreadMessage
from mozg_outreach.storage.db import Database
from mozg_outreach.storage.models import Draft, Item, MailThread, Project, utcnow
from mozg_outreach.storage.repositories import InteractionRepository

logger = logging.getLogger(__name__)

EMAIL = "email"
WATCH_DAYS = 60
POLL_SECONDS = 300.0
_BOUNCER = re.compile(r"^(mailer-daemon|postmaster)@")


class MailDraftError(ValueError):
    """This draft can't go to Gmail: wrong channel, no address, or already sent."""


class ThreadStatus(BaseModel):
    sent_at: datetime | None = None
    replied_at: datetime | None = None
    bounced_at: datetime | None = None
    sent_message_id: str | None = None
    sent_subject: str | None = None

    @classmethod
    def of(
        cls, messages: Sequence[ThreadMessage], own: str, since: datetime | None = None
    ) -> "ThreadStatus":
        """Reads a thread oldest first: our send, then the first reply and the first bounce.

        Only messages from `since` count, for a follow-up sharing an earlier letter's thread.
        """
        status = cls()
        start = since.replace(microsecond=0) if since else None
        for message in messages:
            if message.draft or (start and message.received_at < start):
                continue
            if status.sent_at is None:
                if message.sender == own:
                    status.sent_at = message.received_at
                    status.sent_message_id = message.header_id
                    status.sent_subject = message.subject
            elif _BOUNCER.match(message.sender):
                status.bounced_at = status.bounced_at or message.received_at
            elif message.sender != own:
                status.replied_at = status.replied_at or message.received_at
        return status


class CheckReport(BaseModel):
    checked: int
    logged: list[str]


class GmailDrafts:
    def __init__(self, db: Database, mailbox: Mailbox) -> None:
        self._db = db
        self._mailbox = mailbox
        # A push reads and replaces a thread the poller may be reading; one at a time.
        self._lock = asyncio.Lock()

    @property
    def address(self) -> str:
        return self._mailbox.address

    async def push(self, draft_id: int) -> int:
        """Puts one draft version into Gmail Drafts in place of the letter's unsent one.

        Returns the letter's MailThread id.
        """
        async with self._lock:
            with self._db.session() as session:
                draft = session.get(Draft, draft_id)
                if draft is None:
                    raise LookupError(f"no draft {draft_id}")
                outgoing = _outgoing(session, draft)
                item_id, recipient, step = draft.item_id, draft.recipient, draft.step
                earlier = _thread(session, item_id, recipient, step)
                if earlier is not None and earlier.sent_at is not None:
                    raise MailDraftError(f"this letter was already sent to {earlier.address}")
                replaces = (
                    (earlier.id, earlier.gmail_thread_id, _since(session, earlier))
                    if earlier
                    else None
                )
            if replaces is not None:
                await self._replace(*replaces)
            pushed = await self._mailbox.push(outgoing)
            with self._db.session() as session:
                row = _thread(session, item_id, recipient, step) or MailThread(
                    item_id=item_id, recipient=recipient, step=step
                )
                row.draft_id = draft_id
                row.address = outgoing.to
                row.gmail_thread_id = pushed.thread_id
                row.pushed_at = utcnow()
                session.add(row)
                session.flush()
                logger.info("item %d: draft %d is in Gmail Drafts", item_id, draft_id)
                return row.id

    async def check(self) -> CheckReport:
        """Reads every watched thread once and logs what changed on its lead."""
        async with self._lock:
            cutoff = utcnow() - timedelta(days=WATCH_DAYS)
            with self._db.session() as session:
                watched = {
                    row.id: (row.gmail_thread_id, _since(session, row))
                    for row in session.scalars(
                        select(MailThread).where(
                            MailThread.pushed_at >= cutoff,
                            MailThread.replied_at.is_(None),
                            MailThread.bounced_at.is_(None),
                        )
                    )
                }
            threads = await self._mailbox.threads(sorted({t for t, _ in watched.values()}))
            logged: list[str] = []
            with self._db.session() as session:
                for row_id, (thread_id, since) in watched.items():
                    row = session.get_one(MailThread, row_id)
                    status = ThreadStatus.of(threads.get(thread_id, []), self.address, since)
                    logged += _record(session, row, status)
        if logged:
            logger.info("Gmail check: %s", "; ".join(logged))
        return CheckReport(checked=len(watched), logged=logged)

    async def watch(self, every: float = POLL_SECONDS) -> None:
        """Checks now and then every `every` seconds until cancelled."""
        while True:
            try:
                await self.check()
            except MailError as exc:
                logger.warning("Gmail check failed: %s", exc)
            except Exception:
                # Only the poller marks letters sent, so one bad check must not end it.
                logger.exception("Gmail check crashed")
            await asyncio.sleep(every)

    async def _replace(self, row_id: int, thread_id: int, since: datetime | None) -> None:
        """Drops the letter's earlier Gmail draft, unless the user sent it since the last check."""
        thread = (await self._mailbox.threads([thread_id])).get(thread_id, [])
        status = ThreadStatus.of(thread, self.address, since)
        if status.sent_at is not None:
            with self._db.session() as session:
                row = session.get_one(MailThread, row_id)
                _record(session, row, status)
                address = row.address
            raise MailDraftError(f"this letter was already sent to {address}")
        await self._mailbox.discard(thread_id)


def _outgoing(session: Session, draft: Draft) -> OutgoingDraft:
    if draft.channel != EMAIL:
        raise MailDraftError(f"only email drafts go to Gmail, not {draft.channel}")
    address = _address(session, draft)
    if address is None:
        raise MailDraftError(f"draft {draft.id} has no email address")
    subject, in_reply_to = draft.subject, None
    if not subject:
        if draft.step is None:
            raise MailDraftError(f"draft {draft.id} has no subject")
        subject, in_reply_to = _reply(session, draft, address.lower())
    item = session.get_one(Item, draft.item_id)
    project = session.get_one(Project, item.project_id)
    return OutgoingDraft(
        to=address,
        subject=subject,
        body=draft.body,
        sender_name=project.sender_name,
        in_reply_to=in_reply_to,
    )


def _reply(session: Session, draft: Draft, address: str) -> tuple[str, str]:
    """Subject and Message-ID for a follow-up: it answers the newest letter sent to `address`."""
    sent = session.scalars(
        select(MailThread)
        .where(
            MailThread.item_id == draft.item_id,
            MailThread.address == address,
            MailThread.sent_message_id.is_not(None),
            MailThread.sent_subject.is_not(None),
        )
        .order_by(MailThread.sent_at.desc())
    ).first()
    if sent is None or sent.sent_message_id is None or sent.sent_subject is None:
        raise MailDraftError(
            f"step {draft.step} replies to your earlier email to {address}: send that one "
            "from Gmail first, then check Gmail"
        )
    # Discarding a draft clears its whole Gmail thread, so one unsent reply at a time.
    pending = session.scalar(
        select(MailThread).where(
            MailThread.item_id == draft.item_id,
            MailThread.gmail_thread_id == sent.gmail_thread_id,
            MailThread.sent_at.is_(None),
            MailThread.step != draft.step,
        )
    )
    if pending is not None:
        raise MailDraftError(
            f"step {pending.step} is still unsent in Gmail Drafts in the same thread: "
            f"send it first, or put step {pending.step} in Gmail again"
        )
    subject = sent.sent_subject
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    return subject, sent.sent_message_id


def _since(session: Session, row: MailThread) -> datetime | None:
    """A follow-up in an earlier letter's thread counts only messages from its own push."""
    earlier = session.scalar(
        select(MailThread.id).where(
            MailThread.gmail_thread_id == row.gmail_thread_id,
            MailThread.id != row.id,
            MailThread.pushed_at < row.pushed_at,
        )
    )
    if earlier is None:
        return None
    # SQLite hands back naive datetimes; ours are UTC.
    pushed = row.pushed_at
    return pushed if pushed.tzinfo else pushed.replace(tzinfo=UTC)


def _address(session: Session, draft: Draft) -> str | None:
    """The address the letter was written for; a hand-edited version takes its generated one's."""
    versions = session.scalars(
        select(Draft)
        .where(
            Draft.item_id == draft.item_id,
            Draft.channel == draft.channel,
            Draft.recipient == draft.recipient,
            _same_step(draft.step),
            Draft.version <= draft.version,
        )
        .order_by(Draft.version.desc())
    )
    for version in versions:
        address = version.review.get("address")
        if isinstance(address, str) and address:
            return address
    return None


def _same_step(step: int | None) -> ColumnElement[bool]:
    return Draft.step.is_(None) if step is None else Draft.step == step


def _thread(session: Session, item_id: int, recipient: str, step: int | None) -> MailThread | None:
    return session.scalar(
        select(MailThread).where(
            MailThread.item_id == item_id,
            MailThread.recipient == recipient,
            MailThread.step.is_(None) if step is None else MailThread.step == step,
        )
    )


def _record(session: Session, row: MailThread, status: ThreadStatus) -> list[str]:
    """Copies new times onto the row and logs each on the lead once."""
    logged: list[str] = []
    interactions = InteractionRepository(session)
    for kind, field, note in (
        ("sent", "sent_at", f"Sent from Gmail to {row.address}"),
        ("replied", "replied_at", f"Reply in the Gmail thread with {row.address}"),
        ("bounced", "bounced_at", f"Gmail says {row.address} bounced"),
    ):
        seen: datetime | None = getattr(status, field)
        if seen is None or getattr(row, field) is not None:
            continue
        setattr(row, field, seen)
        if kind != "sent" and _shared(session, row, field):
            continue
        interactions.log(row.item_id, kind, note).happened_at = seen
        logged.append(f"item {row.item_id}: {kind}")
    if row.sent_message_id is None and status.sent_message_id:
        row.sent_message_id, row.sent_subject = status.sent_message_id, status.sent_subject
    return logged


def _shared(session: Session, row: MailThread, field: str) -> bool:
    """Another step in the same Gmail thread already logged this reply or bounce."""
    column = getattr(MailThread, field)
    return (
        session.scalar(
            select(MailThread.id).where(
                MailThread.gmail_thread_id == row.gmail_thread_id,
                MailThread.id != row.id,
                column.is_not(None),
            )
        )
        is not None
    )
