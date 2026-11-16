from uuid import uuid4

import pytest

from mozg_outreach.mail import GmailMailbox, GmailSettings, OutgoingDraft

pytestmark = pytest.mark.live


@pytest.fixture
def mailbox() -> GmailMailbox:
    settings = GmailSettings.from_env()
    if settings is None:
        pytest.skip("Gmail is not set up in the environment")
    return GmailMailbox(settings)


async def test_a_draft_to_yourself_round_trips(mailbox: GmailMailbox) -> None:
    pushed = await mailbox.push(
        OutgoingDraft(
            to=mailbox.address,
            subject=f"mozg-outreach check {uuid4().hex[:8]}",
            body="A test draft from mozg-outreach. The test moves it to Trash.",
        )
    )
    try:
        thread = (await mailbox.threads([pushed.thread_id]))[pushed.thread_id]
    finally:
        moved = await mailbox.discard(pushed.thread_id)

    assert moved == 1
    assert [(m.message_id, m.sender, m.draft) for m in thread] == [
        (pushed.message_id, mailbox.address, True)
    ]
