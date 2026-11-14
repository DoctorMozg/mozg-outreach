"""The run event log: stored for replay, and announced to live listeners."""

import asyncio
from collections import defaultdict

from mozg_outreach.storage.db import Database
from mozg_outreach.storage.models import JsonObject, RunEvent
from mozg_outreach.storage.repositories import RunEventRepository


class RunLog:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._waiters: defaultdict[int, asyncio.Event] = defaultdict(asyncio.Event)

    def append(self, run_id: int, kind: str, payload: JsonObject) -> int:
        with self._db.session() as session:
            seq = RunEventRepository(session).append(run_id, kind, payload).seq
        # Wake every listener, then give the next ones a fresh event to wait on.
        self._waiters.pop(run_id, asyncio.Event()).set()
        return seq

    def after(self, run_id: int, seq: int) -> list[RunEvent]:
        with self._db.session() as session:
            return RunEventRepository(session).after(run_id, seq)

    async def wait(self, run_id: int, timeout: float) -> None:
        """Return when a new event is appended, or after `timeout` seconds."""
        try:
            await asyncio.wait_for(self._waiters[run_id].wait(), timeout)
        except TimeoutError:
            return
