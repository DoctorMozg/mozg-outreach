"""Email letters in the user's Gmail: pushed as drafts, then marked sent, replied or bounced."""

import asyncio
import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from mozg_outreach.cvs.models import CV_CHOICE, CvChoice
from mozg_outreach.mail.gmail import Mailbox
from mozg_outreach.mail.models import Attachment, MailError, OutgoingDraft, ThreadMessage
from mozg_outreach.mail.suppression import BOUNCED, Suppressions
from mozg_outreach.storage.db import Database
from mozg_outreach.storage.models import Cv, Draft, Item, MailThread, Project, utcnow
from mozg_outreach.storage.repositories import InteractionRepository

logger = logging.getLogger(__name__)

EMAIL = "email"
COVER_LETTER = "cover_letter"
# A cover letter goes to Gmail only when it was written for an address, with the CV attached.
GMAIL_CHANNELS = frozenset({EMAIL, COVER_LETTER})
GUESSED = "guessed"
WATCH_DAYS = 60
POLL_SECONDS = 300.0
# A wrong guess bounces, and bounces hurt the sender's reputation with Gmail.
MAX_GUESSED_PER_DAY = 3
# A sudden jump in volume looks like spam to mailbox providers; small days are never a spike.
SPIKE_FACTOR = 2
SPIKE_FLOOR = 5
_BOUNCER = re.compile(r"^(mailer-daemon|postmaster)@")
# A delay notice ("Delivery Status Notification (Delay)") means the server is still retrying.
_DELAYED = re.compile(r"\bdelay(ed)?\b", re.IGNORECASE)


class MailDraftError(ValueError):
    """This draft can't go to Gmail: wrong channel, no address, or already sent."""


class ThreadStatus(BaseModel):
    sent_at: datetime | None = None
    replied_at: datetime | None = None
    bounced_at: datetime | None = None
    auto_replied_at: datetime | None = None
    sent_message_id: str | None = None
    sent_subject: str | None = None
    sent_gmail_id: int | None = Field(default=None, description="Gmail's id of the sent message.")

    @classmethod
    def of(
        cls, messages: Sequence[ThreadMessage], own: str, since: datetime | None = None
    ) -> "ThreadStatus":
        """Reads a thread oldest first: our send, then the first reply, bounce and auto-reply.

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
                    status.sent_gmail_id = message.message_id
            elif _BOUNCER.match(message.sender):
                if not _DELAYED.search(message.subject):
                    status.bounced_at = status.bounced_at or message.received_at
            elif message.auto:
                status.auto_replied_at = status.auto_replied_at or message.received_at
            elif message.sender != own:
                status.replied_at = status.replied_at or message.received_at
        return status


class PushResult(BaseModel):
    thread_id: int = Field(description="The letter's MailThread row.")
    warnings: list[str] = Field(default_factory=list)


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

    async def push(self, draft_id: int, confirm: bool = False) -> PushResult:
        """Puts one draft version into Gmail Drafts in place of the letter's unsent one.

        A guessed address goes only with `confirm`, and only a few a day.
        """
        async with self._lock:
            with self._db.session() as session:
                draft = session.get(Draft, draft_id)
                if draft is None:
                    raise LookupError(f"no draft {draft_id}")
                outgoing, guessed = _outgoing(session, draft)
                notes = _attach_cv(session, draft, outgoing)
                item_id, recipient, step = draft.item_id, draft.recipient, draft.step
                earlier = _thread(session, item_id, recipient, step)
                if earlier is not None and earlier.sent_at is not None:
                    raise MailDraftError(f"this letter was already sent to {earlier.address}")
                if guessed:
                    _guard_guess(session, outgoing.to, confirm, earlier)
                warnings = notes + _volume(session)
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
                return PushResult(thread_id=row.id, warnings=warnings)

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
            unread: dict[int, int] = {}
            with self._db.session() as session:
                for row_id, (thread_id, since) in watched.items():
                    row = session.get_one(MailThread, row_id)
                    status = ThreadStatus.of(threads.get(thread_id, []), self.address, since)
                    logged += _record(session, row, status)
                    if row.sent_body is None and status.sent_gmail_id is not None:
                        unread[row_id] = status.sent_gmail_id
            for row_id, message_id in unread.items():
                await self._keep_sent_text(row_id, message_id)
        if logged:
            logger.info("Gmail check: %s", "; ".join(logged))
        return CheckReport(checked=len(watched), logged=logged)

    async def _keep_sent_text(self, row_id: int, message_id: int) -> None:
        """Stores what the user actually sent; a failure leaves it for the next check."""
        try:
            text = await self._mailbox.text(message_id)
        except MailError as exc:
            logger.warning("could not read sent message %d: %s", message_id, exc)
            return
        with self._db.session() as session:
            session.get_one(MailThread, row_id).sent_body = text

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


def _outgoing(session: Session, draft: Draft) -> tuple[OutgoingDraft, bool]:
    """The message to put in Gmail, and whether its address is a guess."""
    if draft.channel not in GMAIL_CHANNELS:
        raise MailDraftError(f"only email and cover letter drafts go to Gmail, not {draft.channel}")
    found = _address(session, draft)
    if found is None:
        raise MailDraftError(f"draft {draft.id} has no email address")
    address, guessed = found
    if "@" not in address:
        raise MailDraftError(f"draft {draft.id} was written for an application form: {address}")
    blocked = Suppressions(session).blocklist().blocks(address, guessed)
    if blocked:
        raise MailDraftError(f"{address} gets no more letters: {blocked}")
    subject, in_reply_to = draft.subject, None
    if not subject:
        if draft.step is None:
            raise MailDraftError(f"draft {draft.id} has no subject")
        subject, in_reply_to = _reply(session, draft, address.lower())
    item = session.get_one(Item, draft.item_id)
    project = session.get_one(Project, item.project_id)
    outgoing = OutgoingDraft(
        to=address,
        subject=subject,
        body=draft.body,
        sender_name=project.sender_name,
        in_reply_to=in_reply_to,
    )
    return outgoing, guessed


def _attach_cv(session: Session, draft: Draft, outgoing: OutgoingDraft) -> list[str]:
    """Attaches the CV picked for the lead to a cover letter; a warning when there is none."""
    if draft.channel != COVER_LETTER:
        return []
    item = session.get_one(Item, draft.item_id)
    stored = item.details.get(CV_CHOICE)
    cv = session.get(Cv, CvChoice.model_validate(stored).cv_id) if stored else None
    if cv is None:
        logger.warning("item %d: cover letter goes to Gmail without a CV", item.id)
        return ["No CV is picked for this job, so the draft has none attached: attach it in Gmail."]
    outgoing.attachments.append(Attachment(filename=cv.filename, content=cv.pdf))
    return []


def _guard_guess(session: Session, address: str, confirm: bool, earlier: MailThread | None) -> None:
    if not confirm:
        raise MailDraftError(
            f"{address} is a guessed address and may bounce: check it, then confirm the push"
        )
    if earlier is not None:
        return  # Replacing this letter's own draft adds nothing to the day's guesses.
    today = datetime.combine(utcnow().date(), datetime.min.time())
    pushed = session.scalars(select(MailThread).where(MailThread.pushed_at >= today))
    guesses = sum(1 for row in pushed if _guessed(session, row))
    if guesses >= MAX_GUESSED_PER_DAY:
        raise MailDraftError(
            f"already {guesses} guessed addresses in Gmail today, the limit is "
            f"{MAX_GUESSED_PER_DAY}: a bounce costs more than a day's wait"
        )


def _guessed(session: Session, row: MailThread) -> bool:
    draft = session.get(Draft, row.draft_id)
    found = _address(session, draft) if draft else None
    return found is not None and found[1]


def _volume(session: Session) -> list[str]:
    """A warning when today's pushes jump well above the last week's daily average."""
    today = datetime.combine(utcnow().date(), datetime.min.time())
    # SQLite hands back naive datetimes; ours are UTC.
    week = [
        p.replace(tzinfo=None)
        for p in session.scalars(
            select(MailThread.pushed_at).where(MailThread.pushed_at >= today - timedelta(days=7))
        )
    ]
    now = sum(1 for p in week if p >= today) + 1
    average = sum(1 for p in week if p < today) / 7
    if now >= SPIKE_FLOOR and now > SPIKE_FACTOR * average:
        return [
            f"{now} letters into Gmail today against {average:.1f} a day last week; "
            "a sudden jump can land them in spam, so spread them over a few days"
        ]
    return []


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


def _address(session: Session, draft: Draft) -> tuple[str, bool] | None:
    """The address the letter was written for and whether it is a guess.

    A hand-edited version takes its generated one's."""
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
            return address, version.review.get("address_confidence") == GUESSED
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
        ("out_of_office", "auto_replied_at", f"Auto-reply from {row.address}"),
    ):
        seen: datetime | None = getattr(status, field)
        if seen is None or getattr(row, field) is not None:
            continue
        setattr(row, field, seen)
        if kind == BOUNCED:
            Suppressions(session).add(row.address, BOUNCED, row.item_id)
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
