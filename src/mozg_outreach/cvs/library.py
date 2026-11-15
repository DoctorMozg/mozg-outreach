"""The stored CVs as the picker sees them."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from mozg_outreach.cvs.models import PICK_CHARS, CvOption, CvSummary
from mozg_outreach.storage.models import Cv

# One person's variants; past this the picker's input outgrows what a cheap model rates well.
MAX_CVS = 8


class CvLibrary:
    def __init__(self, session: Session) -> None:
        self._session = session

    def all(self) -> list[Cv]:
        return list(self._session.scalars(select(Cv).order_by(Cv.id)))

    def options(self) -> list[CvOption]:
        """The profiled CVs; one without a summary can't be told apart, so it is left out."""
        return [
            CvOption(
                id=cv.id,
                name=cv.name,
                summary=CvSummary.model_validate(cv.summary),
                excerpt=cv.text[:PICK_CHARS],
            )
            for cv in self.all()
            if cv.summary is not None
        ]
