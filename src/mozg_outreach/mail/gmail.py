"""Gmail over IMAP: writes drafts and reads threads. There is no way to send from here."""

import asyncio
import imaplib
import logging
import re
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesHeaderParser, BytesParser
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from typing import Protocol

from mozg_outreach.mail.models import (
    GmailSettings,
    MailError,
    OutgoingDraft,
    PushedDraft,
    ThreadMessage,
)

logger = logging.getLogger(__name__)

HOST = "imap.gmail.com"
TIMEOUT = 30
DRAFTS, ALL, TRASH = r"\Drafts", r"\All", r"\Trash"

_LIST = re.compile(rb'^\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$')
_APPENDUID = re.compile(rb"\[APPENDUID \d+ (\d+)\]")
_NUMBER = {key: re.compile(rf"\b{key} (\d+)".encode()) for key in ("X-GM-MSGID", "X-GM-THRID")}
_FLAGS = re.compile(rb"\bFLAGS \(([^)]*)\)")
_LABELS = re.compile(rb"\bX-GM-LABELS \(((?:[^)\"]|\"[^\"]*\")*)\)")
# Gmail sends the system label bare (\Draft) or quoted with an escaped backslash ("\\Draft").
_DRAFT_LABEL = re.compile(rb'(?:^|[\s(])(?:\\Draft|"\\\\Draft")(?=$|[\s)])')
_INTERNALDATE = re.compile(rb'\bINTERNALDATE "([^"]+)"')
_MESSAGE = re.compile(rb"^\d+ \(")
# Autoresponders that skip RFC 3834 headers still mark the subject, in the reader's language.
_AUTO_SUBJECT = re.compile(
    r"^(?:automatic reply|auto(?:matic)?[- ]?reply|autoreply|auto-response|out of (?:the )?office"
    r"|abwesenheit|automatische antwort|r[ée]ponse automatique|absence|respuesta autom[áa]tica"
    r"|автоответ|автоматический ответ)",
    re.IGNORECASE,
)


class Mailbox(Protocol):
    """The user's mailbox: drafts go in, the user sends them, threads come back."""

    @property
    def address(self) -> str: ...

    async def push(self, draft: OutgoingDraft) -> PushedDraft: ...

    async def discard(self, thread_id: int) -> int: ...

    async def threads(self, thread_ids: Sequence[int]) -> dict[int, list[ThreadMessage]]: ...

    async def text(self, message_id: int) -> str: ...


def build_message(draft: OutgoingDraft, address: str) -> bytes:
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = formataddr((draft.sender_name or "", address))
    message["To"] = draft.to
    message["Subject"] = draft.subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=address.rsplit("@", 1)[1])
    if draft.in_reply_to:
        # Gmail files a reply into the thread when these name its message and the subject matches.
        message["In-Reply-To"] = draft.in_reply_to
        message["References"] = draft.in_reply_to
    message.set_content(draft.body)
    for attachment in draft.attachments:
        message.add_attachment(
            attachment.content, maintype="application", subtype="pdf", filename=attachment.filename
        )
    return message.as_bytes()


class GmailMailbox:
    """Opens one IMAP connection per call, in a worker thread since imaplib blocks."""

    def __init__(
        self, settings: GmailSettings, connect: Callable[[], imaplib.IMAP4] | None = None
    ) -> None:
        self._settings = settings
        self._connect = connect or (lambda: imaplib.IMAP4_SSL(HOST, timeout=TIMEOUT))

    @property
    def address(self) -> str:
        return self._settings.address

    async def push(self, draft: OutgoingDraft) -> PushedDraft:
        return await asyncio.to_thread(self._run, lambda s: s.push(draft))

    async def discard(self, thread_id: int) -> int:
        """Moves the thread's unsent drafts to Trash; returns how many there were."""
        return await asyncio.to_thread(self._run, lambda s: s.discard(thread_id))

    async def threads(self, thread_ids: Sequence[int]) -> dict[int, list[ThreadMessage]]:
        if not thread_ids:
            return {}
        return await asyncio.to_thread(self._run, lambda s: s.threads(thread_ids))

    async def text(self, message_id: int) -> str:
        """The plain-text body of one message, to compare what was sent with the draft."""
        return await asyncio.to_thread(self._run, lambda s: s.text(message_id))

    def _run[TResult](self, work: Callable[["_Session"], TResult]) -> TResult:
        try:
            imap = self._connect()
        except OSError as exc:
            raise MailError(f"could not reach {HOST}: {exc}") from exc
        try:
            try:
                imap.login(self._settings.address, self._settings.app_password.get_secret_value())
            except imaplib.IMAP4.error as exc:
                raise MailError(f"Gmail refused the login for {self.address}: {exc}") from exc
            return work(_Session(imap, self._settings.address))
        except imaplib.IMAP4.error as exc:
            raise MailError(f"Gmail IMAP error: {exc}") from exc
        finally:
            try:
                imap.logout()
            except (imaplib.IMAP4.error, OSError):
                logger.debug("IMAP logout failed", exc_info=True)


class _Session:
    def __init__(self, imap: imaplib.IMAP4, address: str) -> None:
        self._imap = imap
        self._address = address
        self._folders: dict[str, str] | None = None

    def push(self, draft: OutgoingDraft) -> PushedDraft:
        drafts = self._folder(DRAFTS)
        data = _ok(
            self._imap.append(
                drafts,
                r"(\Draft \Seen)",
                imaplib.Time2Internaldate(time.time()),
                build_message(draft, self._address),
            ),
            "APPEND",
        )
        found = _APPENDUID.search(_first(data))
        if found is None:
            raise MailError(f"Gmail did not return the new draft's UID: {data!r}")
        _ok(self._imap.select(drafts, readonly=True), f"SELECT {drafts}")
        fetched = _fetched(self._uid("FETCH", found.group(1).decode(), "(X-GM-MSGID X-GM-THRID)"))
        if not fetched:
            raise MailError("Gmail lost the draft it just stored")
        meta = fetched[0][0]
        pushed = PushedDraft(
            message_id=_number(meta, "X-GM-MSGID"), thread_id=_number(meta, "X-GM-THRID")
        )
        logger.info("pushed a Gmail draft in thread %d", pushed.thread_id)
        return pushed

    def discard(self, thread_id: int) -> int:
        drafts = self._folder(DRAFTS)
        _ok(self._imap.select(drafts), f"SELECT {drafts}")
        uids = self._search(thread_id)
        if uids:
            # Trash keeps it for 30 days, unlike an expunge.
            self._uid("MOVE", ",".join(uids), self._folder(TRASH))
            logger.info("moved %d draft(s) of thread %d to Trash", len(uids), thread_id)
        return len(uids)

    def threads(self, thread_ids: Sequence[int]) -> dict[int, list[ThreadMessage]]:
        everything = self._folder(ALL)
        _ok(self._imap.select(everything, readonly=True), f"SELECT {everything}")
        found: dict[int, list[ThreadMessage]] = {}
        for thread_id in thread_ids:
            uids = self._search(thread_id)
            if not uids:
                found[thread_id] = []
                continue
            # PEEK, so reading the thread doesn't mark the user's mail as read.
            items = (
                "(X-GM-MSGID X-GM-LABELS FLAGS INTERNALDATE "
                "BODY.PEEK[HEADER.FIELDS "
                "(FROM MESSAGE-ID SUBJECT AUTO-SUBMITTED X-AUTOREPLY X-AUTORESPOND)])"
            )
            fetched = _fetched(self._uid("FETCH", ",".join(uids), items))
            found[thread_id] = sorted(
                (_thread_message(meta, header) for meta, header in fetched),
                key=lambda m: m.received_at,
            )
        return found

    def text(self, message_id: int) -> str:
        everything = self._folder(ALL)
        _ok(self._imap.select(everything, readonly=True), f"SELECT {everything}")
        uids = _first(self._uid("SEARCH", "X-GM-MSGID", str(message_id))).decode().split()
        if not uids:
            raise MailError(f"Gmail has no message {message_id}")
        fetched = _fetched(self._uid("FETCH", uids[0], "(BODY.PEEK[])"))
        if not fetched:
            raise MailError(f"Gmail returned no body for message {message_id}")
        return plain_text(fetched[0][1])

    def _search(self, thread_id: int) -> list[str]:
        data = self._uid("SEARCH", "X-GM-THRID", str(thread_id))
        return _first(data).decode().split()

    def _uid(self, command: str, *args: str) -> Sequence[object]:
        return _ok(self._imap.uid(command, *args), f"UID {command}")

    def _folder(self, flag: str) -> str:
        if self._folders is None:
            self._folders = special_folders(_ok(self._imap.list(), "LIST"))
        try:
            return self._folders[flag]
        except KeyError:
            raise MailError(f"Gmail has no {flag} folder; is IMAP access on?") from None


def special_folders(listing: Sequence[object]) -> dict[str, str]:
    """Special-use flag to quoted folder name, since folder names follow the account language."""
    folders: dict[str, str] = {}
    for line in listing:
        if not isinstance(line, bytes) or (found := _LIST.match(line)) is None:
            continue
        name = found["name"].decode()
        quoted = name if name.startswith('"') else f'"{name}"'
        for flag in found["flags"].decode().split():
            if flag in (DRAFTS, ALL, TRASH):
                folders[flag] = quoted
    return folders


def _thread_message(meta: bytes, header: bytes) -> ThreadMessage:
    date = _INTERNALDATE.search(meta)
    if date is None:
        raise MailError(f"FETCH reply has no INTERNALDATE: {meta!r}")
    headers = BytesHeaderParser(policy=policy.default).parsebytes(header)
    header_id = str(headers.get("Message-ID", "")).strip()
    subject = str(headers.get("Subject", ""))
    return ThreadMessage(
        message_id=_number(meta, "X-GM-MSGID"),
        sender=parseaddr(str(headers.get("From", "")))[1].lower(),
        draft=_is_draft(meta),
        received_at=datetime.strptime(date.group(1).decode().strip(), "%d-%b-%Y %H:%M:%S %z"),
        header_id=header_id or None,
        subject=subject,
        auto=is_auto_reply(
            str(headers.get("Auto-Submitted", "")),
            any(headers.get(h) for h in ("X-Autoreply", "X-Autorespond")),
            subject,
        ),
    )


def plain_text(raw: bytes) -> str:
    """The text/plain part of a message, or empty when it has none."""
    message = BytesParser(policy=policy.default).parsebytes(raw)
    if not isinstance(message, EmailMessage):
        return ""
    part = message.get_body(preferencelist=("plain",))
    return str(part.get_content()) if part is not None else ""


def is_auto_reply(auto_submitted: str, autoreply_header: bool, subject: str) -> bool:
    """RFC 3834's Auto-Submitted, the older X-Autoreply headers, or an autoresponder subject."""
    submitted = auto_submitted.strip().lower()
    return (
        bool(submitted and submitted != "no")
        or autoreply_header
        or _AUTO_SUBJECT.match(subject.strip()) is not None
    )


def _is_draft(meta: bytes) -> bool:
    """All Mail leaves the \\Draft flag off a draft; Gmail's \\Draft label is what marks it."""
    flags = _FLAGS.search(meta)
    labels = _LABELS.search(meta)
    return (flags is not None and rb"\Draft" in flags.group(1).split()) or (
        labels is not None and _DRAFT_LABEL.search(labels.group(1)) is not None
    )


def _fetched(data: Sequence[object]) -> list[tuple[bytes, bytes]]:
    """imaplib's FETCH reply as (attributes, literal) per message."""
    messages: list[tuple[bytes, bytes]] = []
    for part in data:
        if isinstance(part, tuple):
            messages.append((part[0], part[1]))
        elif isinstance(part, bytes):
            if _MESSAGE.match(part):
                messages.append((part, b""))
            elif messages:
                # Attributes Gmail sends after the literal, such as b" UID 7)".
                meta, literal = messages[-1]
                messages[-1] = (meta + part, literal)
    return messages


def _number(meta: bytes, key: str) -> int:
    found = _NUMBER[key].search(meta)
    if found is None:
        raise MailError(f"FETCH reply has no {key}: {meta!r}")
    return int(found.group(1))


def _first(data: Sequence[object]) -> bytes:
    head = data[0] if data else b""
    return head if isinstance(head, bytes) else b""


def _ok[TData: Sequence[object]](reply: tuple[str, TData], command: str) -> TData:
    status, data = reply
    if status != "OK":
        raise MailError(f"Gmail rejected {command}: {status} {data!r}")
    return data
