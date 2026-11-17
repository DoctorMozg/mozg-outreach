"""Format and evidence checks every letter must pass, before and after naturalization."""

import re
from typing import Literal, get_args

from pydantic import BaseModel

from mozg_outreach.letters.cold import ColdRules
from mozg_outreach.letters.frame import LetterFrame
from mozg_outreach.letters.models import LetterBrief
from mozg_outreach.naturalize.models import SoftPunctuation
from mozg_outreach.naturalize.punctuation import PunctuationChecker
from mozg_outreach.naturalize.validation import body_word_count

ProblemKind = Literal[
    "subject", "frame", "word_budget", "length", "punctuation", "markdown", "number", "cold_rule"
]

# A digit after a letter is part of a term (B2B, Q3, EC2), not a figure to verify.
_NUMBER = re.compile(r"(?<![A-Za-z])\d+(?:[.,]\d+)*")
# A call length in the ask ("a 15-minute call") is the sender's offer, not a claim about them.
_DURATION = re.compile(r"\d+(?:\s|-)?(?:min|minute|minutes|hour|hours)\b", re.IGNORECASE)
_MARKDOWN = re.compile(r"^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|>)")
_CAPITALIZED = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][\w'&-]*", re.MULTILINE)
_PRONOUNS = frozenset({"I", "I'm", "I'd", "I've", "I'll"})
_CALENDAR = frozenset(
    {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "January", "February", "March"}
    | {"April", "May", "June", "July", "August", "September", "October", "November", "December"}
)


class LetterProblem(BaseModel):
    kind: ProblemKind
    detail: str


class LetterChecker:
    def __init__(self, brief: LetterBrief, frame: LetterFrame) -> None:
        self._brief = brief
        self._frame = frame
        # Soft marks are a voice question for the naturalizer; here only hard bans count.
        self._punctuation = PunctuationChecker(get_args(SoftPunctuation))
        self._evidence = " ".join(
            [brief.model_dump_json(), frame.salutation, frame.signature]
        ).casefold()
        # Whole numbers, so "3" isn't supported just because a URL or "2023" contains a 3.
        self._numbers = set(_NUMBER.findall(self._evidence))
        cold = brief.channel in ("email", "dm") and brief.language == "en"
        self._cold = ColdRules(brief) if cold else None

    def check(self, subject: str | None, text: str) -> list[LetterProblem]:
        problems = [*self._subject(subject), *self._frame_problems(text)]
        words = body_word_count(text)
        budget = self._brief.word_budget
        if not budget.min <= words <= budget.max:
            detail = f"body has {words} words, budget {budget.min}-{budget.max}"
            problems.append(LetterProblem(kind="word_budget", detail=detail))
        limit = self._brief.max_chars
        if limit is not None and len(text) > limit:
            detail = f"{len(text)} characters with greeting and sign-off, limit {limit}"
            problems.append(LetterProblem(kind="length", detail=detail))
        for issue in self._punctuation.check("\n".join([subject or "", text])):
            detail = f"{issue.name} {issue.char!r}"
            problems.append(LetterProblem(kind="punctuation", detail=detail))
        if any(_MARKDOWN.match(line) for line in text.splitlines()):
            problems.append(LetterProblem(kind="markdown", detail="body uses markdown"))
        for number in self.unsupported_numbers(subject, text):
            detail = f"{number!r} is not in the evidence"
            problems.append(LetterProblem(kind="number", detail=detail))
        if self._cold:
            for detail in self._cold.problems(subject, self._body(text)):
                problems.append(LetterProblem(kind="cold_rule", detail=detail))
        return problems

    def warnings(self, text: str) -> list[str]:
        """Style notes for the user's eye; they never send a draft back."""
        return self._cold.warnings(self._body(text)) if self._cold else []

    def unsupported_numbers(self, subject: str | None, text: str) -> list[str]:
        written = _DURATION.sub(" ", f"{subject or ''}\n{text}")
        return sorted(set(_NUMBER.findall(written)) - self._numbers)

    def unverified_names(self, text: str) -> list[str]:
        """Capitalized words mid-sentence that no source mentions; for review, not rejection."""
        words = {w.removesuffix("'s") for w in _CAPITALIZED.findall(text)}
        names = words - _PRONOUNS - _CALENDAR
        return sorted(n for n in names if n and n.casefold() not in self._evidence)

    def _subject(self, subject: str | None) -> list[LetterProblem]:
        limit = self._brief.subject_max_chars
        if limit is None:
            if subject:
                return [LetterProblem(kind="subject", detail="this channel has no subject")]
            return []
        if not subject or not subject.strip():
            return [LetterProblem(kind="subject", detail="email needs a subject")]
        if len(subject) > limit:
            detail = f"subject has {len(subject)} characters, limit {limit}"
            return [LetterProblem(kind="subject", detail=detail)]
        return []

    def _body(self, text: str) -> str:
        frame = {self._frame.salutation, self._frame.closing, self._frame.signature}
        return "\n".join(line for line in text.splitlines() if line.strip() not in frame).strip()

    def _frame_problems(self, text: str) -> list[LetterProblem]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        expected_end = [self._frame.closing, self._frame.signature]
        if len(lines) < 4 or lines[0] != self._frame.salutation or lines[-2:] != expected_end:
            detail = f"must open with {self._frame.salutation!r} and end with {expected_end}"
            return [LetterProblem(kind="frame", detail=detail)]
        return []
