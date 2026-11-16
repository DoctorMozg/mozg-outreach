import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from mozg_outreach.domain import ItemKind
from mozg_outreach.storage.models import Draft, Item, JsonObject, MailThread
from tests.api.conftest import Api, open_api
from tests.mail.conftest import ME, FakeMailbox
from tests.pipelines.conftest import RecordedRunner

ANN = "ann@client.example"
GENERATED: JsonObject = {"address": ANN, "address_confidence": "published"}
SENT_AT = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)


class Mail:
    def __init__(self, api: Api, box: FakeMailbox) -> None:
        self.api = api
        self.box = box

    async def letter(self, channel: str = "email", review: JsonObject = GENERATED) -> int:
        """One company item with one letter to Ann; returns the draft id."""
        project_id = await self.api.project("biz")
        with self.api.services.db.session() as session:
            item = Item(
                project_id=project_id, kind=ItemKind.COMPANY, dedup_key="acme", title="Acme"
            )
            session.add(item)
            session.flush()
            return self.draft(session, item.id, channel=channel, review=review)

    def draft(
        self,
        session: Session,
        item_id: int,
        *,
        version: int = 1,
        channel: str = "email",
        review: JsonObject = GENERATED,
        step: int | None = None,
        subject: str | None = "Pricing data",
    ) -> int:
        row = Draft(
            item_id=item_id, channel=channel, recipient="Ann Lee", step=step, version=version
        )
        row.subject, row.body, row.review = subject, f"Hi Ann,\n\nv{version}.", review
        session.add(row)
        session.flush()
        return row.id

    async def push(self, draft_id: int) -> JsonObject:
        return await self.api.ok("POST", f"/api/drafts/{draft_id}/gmail")

    async def item(self, draft_id: int) -> JsonObject:
        with self.api.services.db.session() as session:
            item_id = session.get_one(Draft, draft_id).item_id
        return await self.api.ok("GET", f"/api/items/{item_id}")

    async def check(self) -> JsonObject:
        return await self.api.ok("POST", "/api/mail/check")


@pytest.fixture
async def mail(tmp_path: Path) -> AsyncIterator[Mail]:
    box = FakeMailbox()
    async for api in open_api(tmp_path, RecordedRunner({}), mailbox=box):
        yield Mail(api, box)


def kinds(item: JsonObject) -> list[object]:
    interactions = item["interactions"]
    assert isinstance(interactions, list)
    return [i["kind"] for i in interactions]


class TestPush:
    async def test_puts_the_letter_in_gmail_drafts(self, mail: Mail) -> None:
        draft_id = await mail.letter()

        pushed = await mail.push(draft_id)

        assert (pushed["address"], pushed["draft_id"], pushed["sent_at"]) == (ANN, draft_id, None)
        (outgoing,) = mail.box.pushed
        assert (outgoing.to, outgoing.subject, outgoing.sender_name) == (
            ANN,
            "Pricing data",
            "Stan Petrov",
        )
        assert (await mail.item(draft_id))["mail"] == [pushed]
        assert (await mail.api.ok("GET", "/api/mail"))["address"] == ME

    async def test_a_newer_version_replaces_the_unsent_gmail_draft(self, mail: Mail) -> None:
        first = await mail.letter()
        old_thread = (await mail.push(first))["id"]
        with mail.api.services.db.session() as session:
            item_id = session.get_one(Draft, first).item_id
            # A hand edit keeps no address; the generated version's is used.
            edited = mail.draft(session, item_id, version=2, review={"edited_by_user": True})
            gmail_thread = session.get_one(MailThread, old_thread).gmail_thread_id

        again = await mail.push(edited)

        assert again["id"] == old_thread
        assert again["draft_id"] == edited
        assert mail.box.threads_by_id[gmail_thread] == []
        assert [d.body for d in mail.box.pushed] == ["Hi Ann,\n\nv1.", "Hi Ann,\n\nv2."]

    async def test_each_step_to_the_same_person_is_its_own_gmail_draft(self, mail: Mail) -> None:
        first = await mail.letter()
        with mail.api.services.db.session() as session:
            item_id = session.get_one(Draft, first).item_id
            # A later step's hand edit takes the address of that step's generated version.
            mail.draft(
                session, item_id, step=3, review={**GENERATED, "address": "ann@other.example"}
            )
            later = mail.draft(session, item_id, step=3, version=2, review={"edited_by_user": True})

        pushed = [await mail.push(first), await mail.push(later)]

        assert [(p["step"], p["address"]) for p in pushed] == [
            (None, ANN),
            (3, "ann@other.example"),
        ]
        assert len({p["id"] for p in pushed}) == 2
        assert len(mail.box.pushed) == 2

    async def test_a_letter_sent_since_the_last_check_is_not_pushed_again(self, mail: Mail) -> None:
        draft_id = await mail.letter()
        await mail.push(draft_id)
        mail.box.send(1000, SENT_AT)

        refused = await mail.api.client.post(f"/api/drafts/{draft_id}/gmail")

        assert refused.status_code == 409
        assert "already sent to ann@client.example" in refused.text
        assert len(mail.box.pushed) == 1
        assert kinds(await mail.item(draft_id)) == ["sent"]

    @pytest.mark.parametrize(
        ("channel", "review", "problem"),
        [
            ("linkedin", GENERATED, "only email drafts"),
            ("email", {"edited_by_user": True}, "has no email address"),
        ],
    )
    async def test_letters_gmail_cant_take(
        self, mail: Mail, channel: str, review: JsonObject, problem: str
    ) -> None:
        draft_id = await mail.letter(channel=channel, review=review)

        refused = await mail.api.client.post(f"/api/drafts/{draft_id}/gmail")

        assert refused.status_code == 409
        assert problem in refused.text
        assert mail.box.pushed == []

    async def test_gmail_errors_are_upstream_failures(self, mail: Mail) -> None:
        draft_id = await mail.letter()
        mail.box.offline = True

        failed = await mail.api.client.post(f"/api/drafts/{draft_id}/gmail")

        assert failed.status_code == 502
        assert "could not reach imap.gmail.com" in failed.text

    async def test_unknown_draft(self, mail: Mail) -> None:
        assert (await mail.api.client.post("/api/drafts/999/gmail")).status_code == 404


class TestFollowUps:
    """Steps after the opener reply in its Gmail thread instead of starting a new one."""

    async def sequence(self, mail: Mail) -> tuple[int, int, int]:
        """An opener and two follow-ups to Ann; the follow-ups have no subject of their own."""
        opener = await mail.letter()
        with mail.api.services.db.session() as session:
            item_id = session.get_one(Draft, opener).item_id
            session.get_one(Draft, opener).step = 1
            follow_ups = [mail.draft(session, item_id, step=s, subject=None) for s in (2, 3)]
        return opener, follow_ups[0], follow_ups[1]

    async def test_waits_until_the_opener_is_sent(self, mail: Mail) -> None:
        opener, follow_up, _ = await self.sequence(mail)
        await mail.push(opener)

        refused = await mail.api.client.post(f"/api/drafts/{follow_up}/gmail")

        assert refused.status_code == 409
        assert "send that one from Gmail first" in refused.text
        assert len(mail.box.pushed) == 1

    async def test_replies_in_the_thread_and_is_tracked_on_its_own(self, mail: Mail) -> None:
        opener, follow_up, _ = await self.sequence(mail)
        first = await mail.push(opener)
        mail.box.send(1000, SENT_AT)
        await mail.check()

        second = await mail.push(follow_up)

        reply = mail.box.pushed[-1]
        assert (reply.subject, reply.in_reply_to) == ("Re: Pricing data", "<1001@mail>")
        with mail.api.services.db.session() as session:
            threads = [session.get_one(MailThread, t["id"]) for t in (first, second)]
            assert threads[0].gmail_thread_id == threads[1].gmail_thread_id
        assert second["sent_at"] is None, "the opener's send is not the follow-up's"
        assert (await mail.check())["logged"] == []

        now = datetime.now(UTC)
        mail.box.send(1000, now + timedelta(minutes=1))
        mail.box.receive(1000, ANN, now + timedelta(hours=2))
        logged = (await mail.check())["logged"]

        item_id = second["item_id"]
        assert isinstance(logged, list)
        assert sorted(map(str, logged)) == [f"item {item_id}: replied", f"item {item_id}: sent"]
        assert kinds(await mail.item(opener)) == ["sent", "sent", "replied"]

    async def test_one_unsent_reply_per_thread_at_a_time(self, mail: Mail) -> None:
        opener, follow_up, breakup = await self.sequence(mail)
        await mail.push(opener)
        mail.box.send(1000, SENT_AT)
        await mail.check()
        await mail.push(follow_up)

        refused = await mail.api.client.post(f"/api/drafts/{breakup}/gmail")

        assert refused.status_code == 409
        assert "step 2 is still unsent in Gmail Drafts" in refused.text


class TestCheck:
    async def test_logs_sent_then_replied_once_each(self, mail: Mail) -> None:
        draft_id = await mail.letter()
        item_id = (await mail.push(draft_id))["item_id"]
        assert (await mail.check())["logged"] == []

        mail.box.send(1000, SENT_AT)
        mail.box.receive(1000, ANN, SENT_AT + timedelta(hours=3))
        first = await mail.check()
        second = await mail.check()

        assert first["logged"] == [f"item {item_id}: sent", f"item {item_id}: replied"]
        assert (second["checked"], second["logged"]) == (0, [])
        item = await mail.item(draft_id)
        assert kinds(item) == ["sent", "replied"]
        mail_threads = item["mail"]
        assert isinstance(mail_threads, list)
        (thread,) = mail_threads
        assert str(thread["sent_at"]).startswith("2026-11-16T09:00:00")
        assert str(thread["replied_at"]).startswith("2026-11-16T12:00:00")

    async def test_a_bounce_is_not_a_reply(self, mail: Mail) -> None:
        draft_id = await mail.letter()
        await mail.push(draft_id)
        mail.box.send(1000, SENT_AT)
        mail.box.receive(1000, "mailer-daemon@googlemail.com", SENT_AT + timedelta(minutes=1))

        await mail.check()

        assert kinds(await mail.item(draft_id)) == ["sent", "bounced"]

    async def test_old_threads_are_no_longer_watched(self, mail: Mail) -> None:
        draft_id = await mail.letter()
        pushed = await mail.push(draft_id)
        with mail.api.services.db.session() as session:
            row = session.get_one(MailThread, pushed["id"])
            row.pushed_at = datetime.now(UTC) - timedelta(days=61)
        mail.box.send(1000, SENT_AT)

        assert (await mail.check())["checked"] == 0
        assert kinds(await mail.item(draft_id)) == []

    async def test_the_poller_keeps_going_after_a_failed_check(self, mail: Mail) -> None:
        draft_id = await mail.letter()
        await mail.push(draft_id)
        mail.box.send(1000, SENT_AT)
        mail.box.offline = True
        gmail = mail.api.services.gmail
        assert gmail is not None

        poller = asyncio.create_task(gmail.watch(every=0.01))
        try:
            await asyncio.sleep(0.05)
            mail.box.offline = False
            async with asyncio.timeout(2):
                while kinds(await mail.item(draft_id)) != ["sent"]:
                    await asyncio.sleep(0.01)
        finally:
            poller.cancel()


async def test_without_gmail_the_endpoints_say_how_to_set_it_up(api: Api) -> None:
    assert (await api.ok("GET", "/api/mail"))["address"] is None
    refused = await api.client.post("/api/mail/check")
    assert refused.status_code == 409
    assert "MOZG_OUTREACH_GMAIL_APP_PASSWORD" in refused.text
