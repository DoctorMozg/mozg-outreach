from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from mozg_outreach.apply.answers import Answerer
from mozg_outreach.apply.filler import Application
from mozg_outreach.apply.finder import FormFinder
from mozg_outreach.apply.models import FillReport

# Called with the confirmation page's URL once the applicant has submitted.
type OnSent = Callable[[str], None]


@dataclass(frozen=True)
class ApplyAgents:
    """The paid steps of one application: answering questions, and finding a hidden form."""

    answerer: Answerer
    finder: FormFinder


class ApplyDesk(Protocol):
    """Opens an application where the applicant can see it, fills it and watches for the send."""

    async def prepare(
        self, item_id: int, url: str, application: Application, agents: ApplyAgents, on_sent: OnSent
    ) -> FillReport: ...

    async def close(self, item_id: int) -> None: ...
