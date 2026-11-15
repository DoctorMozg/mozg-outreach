"""Which language a posting is in, so its letter is written in the same one."""

import re

from pydantic import BaseModel

from mozg_outreach.letters.frame import letter_language

# Words common in any posting and rare in the other languages listed here.
_STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset({"the", "and", "to", "of", "for", "with", "you", "we", "our", "is", "are"}),
    "de": frozenset({"und", "der", "die", "das", "mit", "für", "wir", "sie", "ist", "eine", "zu"}),
    "fr": frozenset({"le", "les", "et", "des", "pour", "nous", "vous", "est", "une", "avec"}),
    "es": frozenset({"el", "los", "las", "y", "para", "con", "que", "una", "nuestro", "por"}),
}
_UKRAINIAN = re.compile(r"[іїєґ]", re.IGNORECASE)
_RUSSIAN = re.compile(r"[ыэъё]", re.IGNORECASE)
_CYRILLIC = re.compile(r"[а-яёіїєґ]", re.IGNORECASE)
_LATIN = re.compile(r"[a-zà-ÿ]", re.IGNORECASE)
_WORD = re.compile(r"[^\W\d_]+")
# Russian and Ukrainian postings keep stack names in Latin, so a third of Cyrillic decides.
CYRILLIC_SHARE = 0.3


def detect_language(text: str) -> str:
    """ISO 639-1 code; English when the text gives no sign of anything else."""
    cyrillic, latin = len(_CYRILLIC.findall(text)), len(_LATIN.findall(text))
    if cyrillic and cyrillic >= CYRILLIC_SHARE * (cyrillic + latin):
        return "uk" if len(_UKRAINIAN.findall(text)) > len(_RUSSIAN.findall(text)) else "ru"
    words = [w.casefold() for w in _WORD.findall(text)]
    hits = {code: sum(w in stop for w in words) for code, stop in _STOPWORDS.items()}
    best = max(hits, key=lambda code: hits[code])
    return best if hits[best] > hits["en"] else "en"


class PostingLanguage(BaseModel):
    """A posting's language and the one its letter is written in, English when unframed."""

    posting: str
    letter: str

    @classmethod
    def of(cls, posting_text: str) -> "PostingLanguage":
        posting = detect_language(posting_text)
        return cls(posting=posting, letter=letter_language(posting))

    def notes(self) -> list[str]:
        if self.posting == self.letter:
            return []
        return [
            f"The posting is in '{self.posting}', which has no letter frame: written in English."
        ]
