import imaplib
from collections.abc import Callable
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import EmailMessage

import pytest
from pydantic import SecretStr

from mozg_outreach.mail import (
    GmailMailbox,
    GmailSettings,
    MailConfigError,
    MailError,
    OutgoingDraft,
)
from mozg_outreach.mail.gmail import build_message, is_auto_reply, plain_text, special_folders
from mozg_outreach.mail.models import ADDRESS_VAR, PASSWORD_VAR, Attachment

PASSWORD = "abcdabcdabcdabcd"
DRAFTS = '"[Gmail]/&BCcENQRABD0EPgQyBDgEOgQ4-"'  # a Russian-language account's Drafts
ALL = '"[Gmail]/All Mail"'
TRASH = '"[Gmail]/Trash"'
LISTING: list[object] = [
    b'(\\HasNoChildren) "/" "INBOX"',
    b'(\\HasChildren \\Noselect) "/" "[Gmail]"',
    b'(\\All \\HasNoChildren) "/" "[Gmail]/All Mail"',
    b'(\\Drafts \\HasNoChildren) "/" ' + DRAFTS.encode(),
    b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"',
    b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"',
]
# A thread as Gmail's All Mail returns it: some attributes arrive after the header literal,
# and a draft has no \Draft flag there, only a \Draft label.
THREAD: list[object] = [
    (
        b'1 (X-GM-MSGID 1802 INTERNALDATE "17-Nov-2026 09:00:00 +0400" '
        b"BODY[HEADER.FIELDS (FROM MESSAGE-ID SUBJECT)] {120}",
        b'From: "Ann Lee" <Ann@Client.example>\r\n'
        b"Message-ID: <CAF1x@mail.gmail.com>\r\n"
        b"Subject: Re: Pricing data at Acme\r\n\r\n",
    ),
    b" FLAGS (\\Seen))",
    (
        b'2 (X-GM-MSGID 1801 X-GM-LABELS ("\\\\Draft") FLAGS (\\Seen) '
        b'INTERNALDATE " 7-Nov-2026 10:00:00 +0000" '
        b"BODY[HEADER.FIELDS (FROM)] {28}",
        b"From: Me <me@example.com>\r\n\r\n",
    ),
    b")",
]

type Reply = tuple[str, list[object]]


class FakeImap:
    """Answers like Gmail and records every command after login."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.searches: dict[str, list[object]] = {}
        self.fetch: list[object] = []
        self.refuse_login = False
        self.failing: str | None = None
        self.logged_out = False

    def login(self, user: str, password: str) -> Reply:
        if self.refuse_login:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
        return "OK", [b"Success"]

    def logout(self) -> Reply:
        self.logged_out = True
        return "BYE", [b"LOGOUT Requested"]

    def list(self) -> Reply:
        return "OK", LISTING

    def append(self, mailbox: str, flags: str, date: str, message: bytes) -> Reply:
        self.calls.append(("APPEND", mailbox, flags, message))
        return "OK", [b"[APPENDUID 7 42] (Success)"]

    def select(self, mailbox: str, readonly: bool = False) -> Reply:
        self.calls.append(("SELECT", mailbox, readonly))
        return "OK", [b"3"]

    def uid(self, command: str, *args: str) -> Reply:
        self.calls.append((command, *args))
        if command == self.failing:
            return "NO", [b"[CANNOT] Something went wrong"]
        if command == "SEARCH":
            return "OK", self.searches.get(args[1], [b""])
        if command == "FETCH":
            return "OK", self.fetch
        return "OK", [None]


@pytest.fixture
def imap() -> FakeImap:
    return FakeImap()


@pytest.fixture
def mailbox(imap: FakeImap) -> GmailMailbox:
    settings = GmailSettings(address="me@example.com", app_password=SecretStr(PASSWORD))
    connect: Callable[[], imaplib.IMAP4] = lambda: imap  # type: ignore[assignment,return-value]  # noqa: E731
    return GmailMailbox(settings, connect)


DRAFT = OutgoingDraft(
    to="Ann@Client.example",
    subject="Pricing data at Acme",
    body="Hi Ann,\n\nCafé facts.",
    sender_name="Stan G",
)


class TestPush:
    async def test_stores_a_draft_and_returns_its_gmail_ids(
        self, mailbox: GmailMailbox, imap: FakeImap
    ) -> None:
        imap.fetch = [b"1 (X-GM-THRID 9001 X-GM-MSGID 9002 UID 42)"]

        pushed = await mailbox.push(DRAFT)

        assert (pushed.thread_id, pushed.message_id) == (9001, 9002)
        command, folder, flags, raw = imap.calls[0]
        assert (command, folder, flags) == ("APPEND", DRAFTS, r"(\Draft \Seen)")
        assert ("SELECT", DRAFTS, True) in imap.calls
        assert ("FETCH", "42", "(X-GM-MSGID X-GM-THRID)") in imap.calls
        assert isinstance(raw, bytes)
        sent = message_from_bytes(raw, policy=policy.default)
        assert sent["To"] == "ann@client.example"
        assert sent["From"] == "Stan G <me@example.com>"
        assert sent.get_content().replace("\r\n", "\n").strip() == "Hi Ann,\n\nCafé facts."
        assert imap.logged_out

    async def test_a_missing_draft_after_append_is_an_error(
        self, mailbox: GmailMailbox, imap: FakeImap
    ) -> None:
        with pytest.raises(MailError, match="lost the draft"):
            await mailbox.push(DRAFT)


class TestDiscard:
    async def test_moves_the_threads_drafts_to_trash(
        self, mailbox: GmailMailbox, imap: FakeImap
    ) -> None:
        imap.searches["9001"] = [b"42 43"]

        assert await mailbox.discard(9001) == 2
        assert ("SELECT", DRAFTS, False) in imap.calls
        assert ("MOVE", "42,43", TRASH) in imap.calls

    async def test_a_sent_draft_leaves_nothing_to_move(
        self, mailbox: GmailMailbox, imap: FakeImap
    ) -> None:
        assert await mailbox.discard(9001) == 0
        assert not [c for c in imap.calls if c[0] == "MOVE"]


class TestThreads:
    async def test_reads_senders_drafts_and_dates_in_order(
        self, mailbox: GmailMailbox, imap: FakeImap
    ) -> None:
        imap.searches = {"9001": [b"5 6"], "9003": [b""]}
        imap.fetch = THREAD

        found = await mailbox.threads([9001, 9003])

        assert found[9003] == []
        first, reply = found[9001]
        assert (first.message_id, first.sender, first.draft) == (1801, "me@example.com", True)
        assert first.received_at == datetime(2026, 11, 7, 10, tzinfo=UTC)
        assert (reply.message_id, reply.sender, reply.draft) == (1802, "ann@client.example", False)
        assert (reply.header_id, reply.subject) == (
            "<CAF1x@mail.gmail.com>",
            "Re: Pricing data at Acme",
        )
        assert (first.header_id, first.subject) == (None, "")
        assert ("SELECT", ALL, True) in imap.calls
        fetches = [c for c in imap.calls if c[0] == "FETCH"]
        assert fetches == [
            (
                "FETCH",
                "5,6",
                "(X-GM-MSGID X-GM-LABELS FLAGS INTERNALDATE "
                "BODY.PEEK[HEADER.FIELDS "
                "(FROM MESSAGE-ID SUBJECT AUTO-SUBMITTED X-AUTOREPLY X-AUTORESPOND)])",
            )
        ]

    async def test_no_threads_needs_no_connection(self, imap: FakeImap) -> None:
        def refuse() -> imaplib.IMAP4:
            raise AssertionError("connected")

        settings = GmailSettings(address="me@example.com", app_password=SecretStr(PASSWORD))
        assert await GmailMailbox(settings, refuse).threads([]) == {}

    @pytest.mark.parametrize(
        ("attributes", "draft"),
        [
            (b"X-GM-LABELS (\\Draft) FLAGS ()", True),
            (b'X-GM-LABELS ("\\\\Important" "\\\\Draft")', True),
            (b"X-GM-LABELS () FLAGS (\\Seen \\Draft)", True),
            (b'X-GM-LABELS ("\\\\Sent" "Drafts of 2026") FLAGS (\\Seen)', False),
        ],
    )
    async def test_a_draft_is_known_by_its_label_or_flag(
        self, mailbox: GmailMailbox, imap: FakeImap, attributes: bytes, draft: bool
    ) -> None:
        imap.searches = {"9001": [b"5"]}
        imap.fetch = [
            (
                b"1 (X-GM-MSGID 1801 " + attributes + b' INTERNALDATE "7-Nov-2026 10:00:00 +0000" '
                b"BODY[HEADER.FIELDS (FROM)] {28}",
                b"From: Me <me@example.com>\r\n\r\n",
            ),
            b")",
        ]

        (message,) = (await mailbox.threads([9001]))[9001]

        assert message.draft is draft


class TestText:
    async def test_reads_the_sent_messages_text_by_its_gmail_id(
        self, imap: FakeImap, mailbox: GmailMailbox
    ) -> None:
        sent = EmailMessage()
        sent.set_content("Hi Ann,\n\nWhat I sent.\n")
        imap.searches["1802"] = [b"9"]
        imap.fetch = [(b"9 (UID 9 BODY[] {64}", sent.as_bytes()), b")"]

        assert await mailbox.text(1802) == "Hi Ann,\n\nWhat I sent.\n"
        assert imap.calls == [
            ("SELECT", ALL, True),
            ("SEARCH", "X-GM-MSGID", "1802"),
            ("FETCH", "9", "(BODY.PEEK[])"),
        ]

    async def test_a_missing_message_is_an_error(
        self, imap: FakeImap, mailbox: GmailMailbox
    ) -> None:
        with pytest.raises(MailError, match="no message 1802"):
            await mailbox.text(1802)


class TestErrors:
    async def test_refused_login_names_the_account_not_the_password(
        self, mailbox: GmailMailbox, imap: FakeImap
    ) -> None:
        imap.refuse_login = True

        with pytest.raises(MailError, match=r"refused the login for me@example\.com") as caught:
            await mailbox.discard(1)
        assert PASSWORD not in str(caught.value)
        assert imap.logged_out

    async def test_a_rejected_command_is_an_error(
        self, mailbox: GmailMailbox, imap: FakeImap
    ) -> None:
        imap.failing = "SEARCH"

        with pytest.raises(MailError, match="rejected UID SEARCH"):
            await mailbox.discard(1)

    async def test_unreachable_server(self) -> None:
        def offline() -> imaplib.IMAP4:
            raise OSError("Network is unreachable")

        settings = GmailSettings(address="me@example.com", app_password=SecretStr(PASSWORD))
        with pytest.raises(MailError, match=r"could not reach imap\.gmail\.com"):
            await GmailMailbox(settings, offline).discard(1)


class TestFolders:
    def test_found_by_special_use_flag_not_name(self) -> None:
        assert special_folders(LISTING) == {r"\All": ALL, r"\Drafts": DRAFTS, r"\Trash": TRASH}

    def test_unquoted_names_get_quoted(self) -> None:
        assert special_folders([b'(\\Trash) "/" Bin']) == {r"\Trash": '"Bin"'}


class TestMessage:
    def test_is_plain_text_with_crlf_lines_and_a_message_id(self) -> None:
        raw = build_message(DRAFT, "me@example.com")

        assert b"\r\n" in raw
        assert b"\n" not in raw.replace(b"\r\n", b"")
        parsed = message_from_bytes(raw, policy=policy.default)
        assert parsed.get_content_type() == "text/plain"
        assert parsed["Subject"] == "Pricing data at Acme"
        assert parsed["Message-ID"].endswith("@example.com>")
        assert parsed["In-Reply-To"] is None

    def test_a_follow_up_names_the_letter_it_answers(self) -> None:
        reply = DRAFT.model_copy(
            update={"subject": "Re: Pricing data at Acme", "in_reply_to": "<CAF1x@mail.gmail.com>"}
        )

        parsed = message_from_bytes(build_message(reply, "me@example.com"), policy=policy.default)

        assert parsed["In-Reply-To"] == parsed["References"] == "<CAF1x@mail.gmail.com>"
        assert parsed["Subject"] == "Re: Pricing data at Acme"

    def test_a_cv_goes_in_as_a_pdf_attachment(self) -> None:
        cv = Attachment(filename="Stan Petrov CV.pdf", content=b"%PDF-1.7 cv")
        with_cv = DRAFT.model_copy(update={"attachments": [cv]})

        parsed = message_from_bytes(build_message(with_cv, "me@example.com"), policy=policy.default)

        assert parsed.get_content_type() == "multipart/mixed"
        [body, attached] = list(parsed.iter_parts())
        assert body.get_content().replace("\r\n", "\n") == DRAFT.body + "\n"
        assert (attached.get_content_type(), attached.get_filename()) == (
            "application/pdf",
            "Stan Petrov CV.pdf",
        )
        assert attached.get_content() == b"%PDF-1.7 cv"


class TestSettings:
    def test_unset_means_gmail_is_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ADDRESS_VAR, raising=False)
        monkeypatch.delenv(PASSWORD_VAR, raising=False)
        assert GmailSettings.from_env() is None

    def test_reads_both_and_drops_the_spaces_google_shows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ADDRESS_VAR, " Me@Example.com ")
        monkeypatch.setenv(PASSWORD_VAR, "abcd abcd abcd abcd")

        found = GmailSettings.from_env()

        assert found is not None
        assert found.address == "me@example.com"
        assert found.app_password.get_secret_value() == PASSWORD

    @pytest.mark.parametrize(
        ("address", "password", "problem"),
        [
            ("me@example.com", "", PASSWORD_VAR),
            ("", PASSWORD, ADDRESS_VAR),
            ("not an address", PASSWORD, "not an email address"),
        ],
    )
    def test_half_set_or_malformed_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch, address: str, password: str, problem: str
    ) -> None:
        monkeypatch.setenv(ADDRESS_VAR, address)
        monkeypatch.setenv(PASSWORD_VAR, password)

        with pytest.raises(MailConfigError, match=problem):
            GmailSettings.from_env()


@pytest.mark.parametrize(
    ("auto_submitted", "autoreply", "subject", "auto"),
    [
        ("auto-replied", False, "Re: Pricing data", True),
        ("no", False, "Re: Pricing data", False),
        ("", True, "Re: Pricing data", True),
        ("", False, "Automatic reply: Pricing data", True),
        ("", False, "Out of Office: Pricing data", True),
        ("", False, "Abwesenheitsnotiz: Pricing data", True),
        ("", False, "Re: out of office until Monday?", False),
        ("", False, "Re: Pricing data", False),
    ],
)
def test_auto_replies_are_known_by_header_or_subject(
    auto_submitted: str, autoreply: bool, subject: str, auto: bool
) -> None:
    assert is_auto_reply(auto_submitted, autoreply, subject) is auto


def test_plain_text_takes_the_text_part_of_a_multipart_message() -> None:
    message = EmailMessage()
    message.set_content("Hi Ann,\n\nThe text I sent.\n")
    message.add_alternative("<p>Hi Ann,</p><p>The text I sent.</p>", subtype="html")
    assert plain_text(message.as_bytes()) == "Hi Ann,\n\nThe text I sent.\n"
    html_only = EmailMessage()
    html_only.set_content("<p>Hi</p>", subtype="html")
    assert plain_text(html_only.as_bytes()) == ""
