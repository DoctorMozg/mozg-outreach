"""Accept a naturalizer rewrite only if it kept what matters; otherwise keep the original text."""

import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from mozg_outreach.naturalize.models import NaturalizeInput, NaturalizeResult
from mozg_outreach.naturalize.punctuation import PunctuationChecker, straighten_quotes

logger = logging.getLogger(__name__)

SPECIFICITY_MARKER = "[SPECIFICITY NEEDED]"
_WORD = re.compile(r"[^\W_]+(?:['-][^\W_]+)*")
_ENTITY = re.compile(r"https?://\S+[^\s.,;:!?)]|[\w.+-]+@[\w-]+(?:\.[\w-]+)+|\d+(?:[.,]\d+)*%?")
_SENTENCE = re.compile(r"[^.!?\n]*\[SPECIFICITY NEEDED\][^.!?\n]*[.!?]?")

ProblemKind = Literal["length_drift", "word_budget", "dropped_term", "frame_changed", "punctuation"]


class RewriteProblem(BaseModel):
    kind: ProblemKind
    key: str
    detail: str


class RewriteOutcome(BaseModel):
    text: str
    reverted: bool
    damage: list[RewriteProblem]
    unresolved: list[RewriteProblem]
    review_notes: list[str]
    changes: list[str] = Field(default_factory=list, description="Empty when reverted.")


class RewriteValidator:
    """Checks one rewrite against the rules it was requested with."""

    def __init__(self, request: NaturalizeInput) -> None:
        self._request = request
        self._original = request.text
        self._punctuation = PunctuationChecker(request.allowed_punctuation, request.preserve)
        self._terms = sorted({*request.preserve, *_ENTITY.findall(request.text)} - {""})

    def accept(self, result: NaturalizeResult) -> RewriteOutcome:
        """Keep the rewrite unless it introduced a problem the original didn't have."""
        notes = [f"Needs a real fact: {claim}" for claim in result.specificity_needed]
        text, marked = _strip_markers(straighten_quotes(result.text))
        notes += [f"Needs a real fact: {sentence}" for sentence in marked]
        before = self.problems(self._original)
        after = self.problems(text)
        known = {p.key for p in before}
        damage = [p for p in after if p.key not in known]
        if damage:
            logger.info("naturalizer rewrite reverted: %s", [p.key for p in damage])
            return RewriteOutcome(
                text=self._original,
                reverted=True,
                damage=damage,
                unresolved=before,
                review_notes=notes,
            )
        return RewriteOutcome(
            text=text,
            reverted=False,
            damage=[],
            unresolved=after,
            review_notes=notes,
            changes=result.changes,
        )

    def keep_original(self, note: str) -> RewriteOutcome:
        return RewriteOutcome(
            text=self._original,
            reverted=True,
            damage=[],
            unresolved=self.problems(self._original),
            review_notes=[note],
        )

    def problems(self, text: str) -> list[RewriteProblem]:
        found = [*self._length(text), *self._dropped(text), *self._frame(text)]
        found += [
            RewriteProblem(
                kind="punctuation",
                key=f"punctuation:{issue.name}",
                detail=f"line {issue.line}: {issue.name} {issue.char!r}",
            )
            for issue in self._punctuation.check(text)
        ]
        return found

    def _length(self, text: str) -> list[RewriteProblem]:
        found: list[RewriteProblem] = []
        old, new = body_word_count(self._original), body_word_count(text)
        drift = 100 * abs(new - old) / old if old else 0.0
        if drift > self._request.length_tolerance_pct:
            limit = self._request.length_tolerance_pct
            detail = f"{old} -> {new} words ({drift:.0f}% change, limit {limit}%)"
            found.append(RewriteProblem(kind="length_drift", key="length_drift", detail=detail))
        budget = self._request.word_budget
        if budget and not budget.min <= new <= budget.max:
            detail = f"{new} words, budget {budget.min}-{budget.max}"
            found.append(RewriteProblem(kind="word_budget", key="word_budget", detail=detail))
        return found

    def _dropped(self, text: str) -> list[RewriteProblem]:
        return [
            RewriteProblem(kind="dropped_term", key=f"dropped:{term}", detail=f"missing {term!r}")
            for term in self._terms
            if term in self._original and term not in text
        ]

    def _frame(self, text: str) -> list[RewriteProblem]:
        """The salutation (first line) and sign-off (last two lines) must survive verbatim."""
        old, new = _lines(self._original), _lines(text)
        if len(old) < 3:
            return []
        found: list[RewriteProblem] = []
        if not new or new[0] != old[0]:
            found.append(_frame_problem("salutation", old[0]))
        if new[-2:] != old[-2:]:
            found.append(_frame_problem("sign_off", " / ".join(old[-2:])))
        return found


def word_count(text: str) -> int:
    return len(_WORD.findall(text))


def body_word_count(text: str) -> int:
    """Budgets count the body only: a framed letter drops its salutation and two sign-off lines."""
    lines = _lines(text)
    return word_count("\n".join(lines[1:-2] if len(lines) >= 3 else lines))


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _frame_problem(part: str, expected: str) -> RewriteProblem:
    return RewriteProblem(
        kind="frame_changed", key=f"frame:{part}", detail=f"expected {expected!r}"
    )


def _strip_markers(text: str) -> tuple[str, list[str]]:
    """Markers must never reach a draft; their sentences go to the review notes instead."""
    if SPECIFICITY_MARKER not in text:
        return text, []
    sentences = [s.replace(SPECIFICITY_MARKER, "").strip() for s in _SENTENCE.findall(text)]
    cleaned = re.sub(r"[ \t]*\[SPECIFICITY NEEDED\][ \t]*", " ", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" +([.,!?])", r"\1", cleaned)
    return "\n".join(line.strip() for line in cleaned.splitlines()), [s for s in sentences if s]
