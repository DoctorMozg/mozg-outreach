from collections.abc import Callable
from typing import Protocol

from mozg_outreach.apply.answers import Answerer
from mozg_outreach.apply.filler import Application
from mozg_outreach.apply.models import FillReport

# Called with the confirmation page's URL once the applicant has submitted.
type OnSent = Callable[[str], None]


class ApplyDesk(Protocol):
    """Opens an application where the applicant can see it, fills it and watches for the send."""

    async def prepare(
        self, item_id: int, url: str, application: Application, answerer: Answerer, on_sent: OnSent
    ) -> FillReport: ...

    async def close(self, item_id: int) -> None: ...
