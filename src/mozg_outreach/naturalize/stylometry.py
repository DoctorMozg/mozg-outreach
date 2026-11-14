"""Measured writing habits of a set of messages, and a distance between two such sets."""

import re
import statistics
from collections import Counter

from pydantic import BaseModel

from mozg_outreach.naturalize.punctuation import straighten_quotes
from mozg_outreach.naturalize.validation import word_count

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
_CONTRACTION = re.compile(r"\b[^\W\d_]+'(?:s|t|re|ve|ll|d|m)\b", re.IGNORECASE)
_FRAME_WORDS = 5


class StyleFeatures(BaseModel):
    samples: int
    words_mean: float
    sentence_words_mean: float
    sentence_words_std: float
    contractions_per_100: float
    commas_per_100: float
    questions_per_100: float
    exclamations_per_100: float
    final_period_rate: float
    greetings: dict[str, int]
    sign_offs: dict[str, int]


# Distance compares only the numeric habits; greetings are reported, not scored.
NUMERIC_FEATURES = (
    "words_mean",
    "sentence_words_mean",
    "sentence_words_std",
    "contractions_per_100",
    "commas_per_100",
    "questions_per_100",
    "exclamations_per_100",
    "final_period_rate",
)


class StyleAnalyzer:
    def features(self, texts: list[str]) -> StyleFeatures:
        if not texts:
            raise ValueError("style features need at least one text")
        texts = [straighten_quotes(t) for t in texts]
        words = sum(word_count(t) for t in texts) or 1
        sentences = [word_count(s) for t in texts for s in _sentences(_body(t)) if word_count(s)]
        body_lines = [line for t in texts for line in _lines(_body(t))]

        def per_100(pattern: str) -> float:
            return round(100 * sum(len(re.findall(pattern, t)) for t in texts) / words, 2)

        return StyleFeatures(
            samples=len(texts),
            words_mean=round(statistics.fmean(word_count(t) for t in texts), 1),
            sentence_words_mean=round(statistics.fmean(sentences), 1) if sentences else 0.0,
            sentence_words_std=round(statistics.pstdev(sentences), 1) if sentences else 0.0,
            contractions_per_100=round(
                100 * sum(len(_CONTRACTION.findall(t)) for t in texts) / words, 2
            ),
            commas_per_100=per_100(","),
            questions_per_100=per_100(r"\?"),
            exclamations_per_100=per_100("!"),
            final_period_rate=round(
                sum(line.endswith(".") for line in body_lines) / len(body_lines), 2
            )
            if body_lines
            else 0.0,
            greetings=dict(Counter(g for t in texts if (g := _greeting(t)))),
            sign_offs=dict(Counter(s for t in texts if (s := _sign_off(t)))),
        )

    @staticmethod
    def distance(a: StyleFeatures, b: StyleFeatures) -> float:
        """Mean relative difference of the numeric habits: 0 is identical, 1 is maximal."""
        gaps = []
        for name in NUMERIC_FEATURES:
            x, y = getattr(a, name), getattr(b, name)
            scale = max(abs(x), abs(y))
            gaps.append(abs(x - y) / scale if scale else 0.0)
        return round(statistics.fmean(gaps), 3)


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _is_frame(line: str) -> bool:
    return len(line.split()) <= _FRAME_WORDS


def _body(text: str) -> str:
    """Drop a short salutation line and a short sign-off block so they don't skew sentences."""
    lines = _lines(text)
    if len(lines) >= 3 and _is_frame(lines[0]) and lines[0].endswith(","):
        lines = lines[1:]
    if len(lines) >= 3 and _is_frame(lines[-1]) and _is_frame(lines[-2]):
        lines = lines[:-2]
    return "\n".join(lines)


def _sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(text) if s.strip()]


def _greeting(text: str) -> str | None:
    lines = _lines(text)
    if len(lines) < 3 or not (_is_frame(lines[0]) and lines[0].endswith(",")):
        return None
    return lines[0].split()[0].strip(",").lower()


def _sign_off(text: str) -> str | None:
    lines = _lines(text)
    if len(lines) < 3 or not (_is_frame(lines[-1]) and _is_frame(lines[-2])):
        return None
    return lines[-2].rstrip(",.!").lower()
