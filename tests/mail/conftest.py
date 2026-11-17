from collections.abc import Sequence
from datetime import UTC, datetime
from itertools import count

from mozg_outreach.mail import MailError, OutgoingDraft, PushedDraft, ThreadMessage

ME = "me@example.com"


class FakeMailbox:
    """Gmail as threads of messages; tests send, reply and bounce by hand."""

    address = ME

    def __init__(self) -> None:
        self.threads_by_id: dict[int, list[ThreadMessage]] = {}
        self.pushed: list[OutgoingDraft] = []
        self.bodies: dict[int, str] = {}
        self.offline = False
        self._ids = count(1000)

    async def push(self, draft: OutgoingDraft) -> PushedDraft:
        """Like Gmail, files a reply into the thread holding the message it answers."""
        self._check()
        new_thread, message_id = next(self._ids), next(self._ids)
        self.pushed.append(draft)
        self.bodies[message_id] = draft.body
        message = self._message(message_id, ME, draft=True, subject=draft.subject)
        answered = [
            t
            for t, thread in self.threads_by_id.items()
            if draft.in_reply_to and any(m.header_id == draft.in_reply_to for m in thread)
        ]
        if answered:
            thread_id = answered[0]
            self.threads_by_id[thread_id].append(message)
        else:
            thread_id = new_thread
            self.threads_by_id[thread_id] = [message]
        return PushedDraft(message_id=message_id, thread_id=thread_id)

    async def discard(self, thread_id: int) -> int:
        self._check()
        thread = self.threads_by_id.get(thread_id, [])
        kept = [m for m in thread if not m.draft]
        self.threads_by_id[thread_id] = kept
        return len(thread) - len(kept)

    async def threads(self, thread_ids: Sequence[int]) -> dict[int, list[ThreadMessage]]:
        self._check()
        return {t: list(self.threads_by_id.get(t, [])) for t in thread_ids}

    async def text(self, message_id: int) -> str:
        self._check()
        return self.bodies[message_id]

    def send(self, thread_id: int, at: datetime, body: str | None = None) -> None:
        """The user clicks Send on the thread's draft, maybe after editing it to `body`."""
        thread = self.threads_by_id[thread_id]
        draft = next(m for m in thread if m.draft)
        if body is not None:
            self.bodies[draft.message_id] = body
        sent = {"draft": False, "received_at": at, "header_id": f"<{draft.message_id}@mail>"}
        thread[thread.index(draft)] = draft.model_copy(update=sent)

    def receive(
        self, thread_id: int, sender: str, at: datetime, *, auto: bool = False, subject: str = ""
    ) -> None:
        message = self._message(next(self._ids), sender, at=at, subject=subject)
        self.threads_by_id[thread_id].append(message.model_copy(update={"auto": auto}))

    def _check(self) -> None:
        if self.offline:
            raise MailError("could not reach imap.gmail.com: Network is unreachable")

    @staticmethod
    def _message(
        message_id: int,
        sender: str,
        *,
        draft: bool = False,
        at: datetime | None = None,
        subject: str = "",
    ) -> ThreadMessage:
        return ThreadMessage(
            message_id=message_id,
            sender=sender,
            draft=draft,
            received_at=at or datetime.now(UTC),
            subject=subject,
        )
