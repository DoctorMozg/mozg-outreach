"""Match a listing's text against the strategy's skills, crediting synonym clusters."""

import re

from pydantic import BaseModel, Field

from mozg_outreach.pipelines.job.models import SkillRules


class SkillHit(BaseModel):
    skill: str
    term: str = Field(description="The word that matched: the skill itself or a synonym.")

    @property
    def canonical(self) -> bool:
        return self.term.casefold() == self.skill.casefold()


class SkillMatch(BaseModel):
    must_haves: list[SkillHit]
    missing_must_haves: list[str]
    or_groups: list[SkillHit]
    missing_or_groups: list[list[str]]
    nice_to_haves: list[SkillHit]

    @property
    def complete(self) -> bool:
        return not self.missing_must_haves and not self.missing_or_groups

    @property
    def gate_passed(self) -> bool:
        """Only a listing naming none of the required skills is off-stack; partial goes on."""
        required = self.must_haves or self.or_groups
        return bool(required) or self.complete


class SkillMatcher:
    def __init__(self, rules: SkillRules) -> None:
        self._rules = rules
        self._patterns: dict[str, list[tuple[str, re.Pattern[str]]]] = {}

    def match(self, text: str) -> SkillMatch:
        must = {s: self._hit(s, text) for s in self._rules.must_have_skills}
        groups = [
            (group, next((h for s in group if (h := self._hit(s, text))), None))
            for group in self._rules.must_have_or_groups
        ]
        nice = [self._hit(s, text) for s in self._rules.nice_to_have_skills]
        return SkillMatch(
            must_haves=[h for h in must.values() if h],
            missing_must_haves=[s for s, h in must.items() if h is None],
            or_groups=[h for _, h in groups if h],
            missing_or_groups=[g for g, h in groups if h is None],
            nice_to_haves=[h for h in nice if h],
        )

    def _hit(self, skill: str, text: str) -> SkillHit | None:
        for term, pattern in self._terms(skill):
            if pattern.search(text):
                return SkillHit(skill=skill, term=term)
        return None

    def _terms(self, skill: str) -> list[tuple[str, re.Pattern[str]]]:
        if skill not in self._patterns:
            synonyms = self._rules.synonym_clusters.get(skill, [])
            self._patterns[skill] = [(t, _pattern(t)) for t in [skill, *synonyms] if t.strip()]
        return self._patterns[skill]


def _pattern(term: str) -> re.Pattern[str]:
    # Short terms like "Go" or "R" are also common words, so they must match in their own case.
    flags = 0 if len(term) <= 3 else re.IGNORECASE
    # A term ending in a symbol (C++, C#) may run into a version: "C++17".
    after = r"(?![+#])" if not term[-1].isalnum() else r"(?![\w+#-])"
    return re.compile(rf"(?<![\w+#.-]){re.escape(term)}{after}", flags)
