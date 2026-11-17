"""Keeps a project's letters from reading like one template: openers and asks vs recent ones."""

from collections.abc import Sequence
from difflib import SequenceMatcher

from pydantic import BaseModel

from mozg_outreach.letters.cold import sentences

RECENT_LETTERS = 30
# Above this difflib ratio two lines read as the same line with the names swapped.
SIMILAR = 0.75


class Phrasing(BaseModel):
    """How one earlier letter opened and what it asked."""

    company: str
    opening: str
    ask: str | None

    @classmethod
    def of(cls, company: str, letter: str) -> "Phrasing | None":
        """Reads a framed letter: skips the salutation; the last question is the ask."""
        paragraphs = [p.strip() for p in letter.strip().split("\n\n") if p.strip()]
        body = paragraphs[1:]
        if not body:
            return None
        opening = next(iter(sentences(body[0])), "")
        if not opening:
            return None
        asks = [s for s in sentences("\n".join(body)) if s.endswith("?")]
        return cls(company=company, opening=opening, ask=asks[-1] if asks else None)


def echoes(recent: Sequence[Phrasing], letter: str) -> list[str]:
    """Review notes for an opening or ask that is close to one sent to another company."""
    mine = Phrasing.of("", letter)
    if mine is None:
        return []
    notes: list[str] = []
    for part, ours, theirs in (
        ("opening", mine.opening, [(p.company, p.opening) for p in recent]),
        ("ask", mine.ask, [(p.company, p.ask) for p in recent if p.ask]),
    ):
        if ours is None:
            continue
        close = next(((c, line) for c, line in theirs if _similar(ours, line)), None)
        if close is not None:
            company, line = close
            notes.append(
                f"The {part} is close to the letter to {company}: {line!r}. "
                "Readers who compare notes spot a template."
            )
    return notes


def _similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, a.casefold(), b.casefold()).ratio() >= SIMILAR
