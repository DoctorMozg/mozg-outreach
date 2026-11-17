"""Rules for English cold emails and DMs, from reply-rate research (2026-11-17).

Hard rules send the draft back to the copywriter; soft ones only become review notes.
"""

import re

from mozg_outreach.letters.models import LetterBrief

SUBJECT_WORDS = (2, 4)
FIRST_SENTENCE_MAX_WORDS = 20
LONG_SENTENCE_WORDS = 22
MAX_GRADE = 7.0
MAX_FACTS = 2

# The preview shows the first sentence; these openings are about the sender or say nothing.
BANNED_STARTS = ("i hope", "i came across", "reaching out", "my name", "i ", "i'm ", "we ")
BUMPS = (
    "just following up",
    "following up on",
    "bumping this",
    "bump this",
    "did you see my",
    "did you get a chance",
    "circling back",
    "just checking in",
    "checking in",
    "touching base",
    "any update",
    "floating this",
    "top of your inbox",
)
ENTHUSIASM = (
    "excited",
    "thrilled",
    "delighted",
    "amazing",
    "incredible",
    "awesome",
    "game-changer",
    "game changer",
    "revolutionary",
    "love to",
)
_MEETING = re.compile(
    r"\b(?:call|meeting|meet|chat|calendar|zoom|demo|catch up|\d+[- ]?min(?:ute)?s?)\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"[^.!?]+[.!?]*")
_WORD = re.compile(r"[A-Za-z0-9][\w'-]*")
_SENDER = frozenset(
    {"i", "i'm", "i've", "i'd", "i'll", "we", "we're", "we've", "our", "us", "me", "my"}
)
_READER = frozenset({"you", "you're", "you've", "you'd", "you'll", "your", "yours"})


class ColdRules:
    """Checks one letter body: no salutation, no sign-off."""

    def __init__(self, brief: LetterBrief) -> None:
        self._brief = brief
        touch = brief.touch
        # A reader's first letter is an opener whether or not an approach plan placed it.
        # A connection note asks nothing; the message after acceptance does.
        note = touch is not None and touch.connection_note
        self._opener = (touch is None or touch.purpose == "open") and not note
        self._follow_up = touch is not None and touch.purpose != "open"
        self._entities = [
            e
            for e in brief.verified_entities
            if len(e) > 2 and e not in (brief.recipient.name, brief.recipient.company)
        ]

    def problems(self, subject: str | None, body: str) -> list[str]:
        found = [*self._subject(subject), *self._first_sentence(body)]
        lowered = body.casefold()
        if self._opener:
            questions = body.count("?")
            if questions != 1:
                found.append(f"an opener asks exactly one question, this has {questions}")
            asks = [s for s in sentences(body) if s.endswith("?") and _MEETING.search(s)]
            if asks:
                found.append(f"a first letter asks about interest, not for a meeting: {asks[0]!r}")
        if self._follow_up:
            bumps = [b for b in BUMPS if b in lowered]
            if bumps:
                found.append(f"a follow-up brings something new, never {bumps[0]!r}")
        return found

    def warnings(self, body: str) -> list[str]:
        notes: list[str] = []
        grade = reading_grade(body)
        if grade > MAX_GRADE:
            notes.append(f"Reads at grade {grade:.0f}; plainer words and shorter sentences help.")
        long = [s for s in sentences(body) if len(words(s)) > LONG_SENTENCE_WORDS]
        if long:
            notes.append(f"{len(long)} sentence(s) over {LONG_SENTENCE_WORDS} words: {long[0]!r}")
        tokens = [w.casefold() for w in words(body)]
        sender = sum(t in _SENDER for t in tokens)
        reader = sum(t in _READER for t in tokens)
        if sender > reader:
            notes.append(f"Talks about the sender more than the reader ({sender} to {reader}).")
        facts = self._facts(body)
        if facts > MAX_FACTS:
            notes.append(f"{facts} sentences carry facts; one or two land better than a list.")
        lowered = body.casefold()
        hype = [w for w in ENTHUSIASM if w in lowered]
        if hype or "!" in body:
            notes.append(f"Enthusiasm reads as a pitch: {', '.join(hype) or '!'}")
        return notes

    def _subject(self, subject: str | None) -> list[str]:
        # Without a subject limit the channel has no subject, which LetterChecker enforces.
        if not subject or self._brief.subject_max_chars is None:
            return []
        low, high = SUBJECT_WORDS
        count = len(subject.split())
        found = []
        if not low <= count <= high:
            found.append(f"subject has {count} words, keep it to {low}-{high}")
        if "?" in subject or any(c.isdigit() for c in subject):
            found.append("subject has a question mark or a digit")
        return found

    def _first_sentence(self, body: str) -> list[str]:
        first = next(iter(sentences(body)), "")
        found = []
        count = len(words(first))
        if count > FIRST_SENTENCE_MAX_WORDS:
            found.append(f"first sentence has {count} words, at most {FIRST_SENTENCE_MAX_WORDS}")
        start = first.casefold()
        banned = next((b for b in BANNED_STARTS if start.startswith(b)), None)
        if banned:
            found.append(f"first sentence starts with {banned.strip()!r}; open on the reader")
        return found

    def _facts(self, body: str) -> int:
        return sum(
            1
            for s in sentences(body)
            if any(c.isdigit() for c in s) or any(e in s for e in self._entities)
        )


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.findall(text.replace("\n", " ")) if words(s)]


def words(text: str) -> list[str]:
    return _WORD.findall(text)


def syllables(word: str) -> int:
    """Vowel groups, minus a silent final e; good enough for a grade estimate."""
    word = word.casefold().strip("'-")
    groups = re.findall(r"[aeiouy]+", word)
    count = len(groups)
    if word.endswith("e") and not word.endswith(("le", "ee")) and count > 1:
        count -= 1
    return max(count, 1)


def reading_grade(text: str) -> float:
    """Flesch-Kincaid grade level."""
    found = sentences(text)
    tokens = words(text)
    if not found or not tokens:
        return 0.0
    per_sentence = len(tokens) / len(found)
    per_word = sum(syllables(w) for w in tokens) / len(tokens)
    return 0.39 * per_sentence + 11.8 * per_word - 15.59
