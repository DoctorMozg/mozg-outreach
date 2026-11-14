"""Deterministic biz lead scoring: six 0-100 axes from agent-extracted features, weighted."""

from collections.abc import Callable
from typing import NamedTuple

from pydantic import BaseModel, Field

from mozg_outreach.domain import FieldSpec, FieldValue, ItemScore, ScoreAxis
from mozg_outreach.pipelines.biz.assessment_models import FitVerdict, SignalCheck, SignalKind
from mozg_outreach.pipelines.biz.enrichment_models import CompanyDossier
from mozg_outreach.pipelines.biz.models import ScoringWeights, SearchSignals

NO_DATA = 20.0
NOT_JUDGED = 50.0
FAILED_MUST_HAVE_CAP = 40.0
TRAJECTORY_POINTS = {
    "rapid_growth": 80.0,
    "steady_growth": 50.0,
    "pivoting": 40.0,
    "stable": 30.0,
    "declining": 10.0,
}
SECTOR_POINTS = {"exact": 100.0, "adjacent": 60.0, "unrelated": 20.0}
ENRICHMENT_SECTIONS = ("reputation", "contacts", "news", "growth", "tech")


class AxisValue(NamedTuple):
    value: float | None
    reason: str


NOT_ENRICHED = AxisValue(None, "not enriched yet")


class BizScoreInput(BaseModel):
    dossier: CompanyDossier | None = Field(description="None until the user enriches the item.")
    verdict: FitVerdict | None
    signals: SearchSignals
    fields: dict[str, FieldValue]
    spec: FieldSpec | None
    weights: ScoringWeights


class BizScorer:
    def score(self, data: BizScoreInput) -> ItemScore:
        weights = data.weights.model_dump()
        weight_sum = sum(weights.values())
        if weight_sum <= 0:
            raise ValueError(f"scoring weights sum to {weight_sum}; at least one must be > 0")
        checks = self._checks(data.verdict, data.signals)
        values = {
            "data_completeness": self.data_completeness(data.dossier, data.fields, data.spec),
            "review_reputation": self._enriched(data.dossier, self.review_reputation),
            "contact_availability": self._enriched(data.dossier, self.contact_availability),
            "growth_signals": self._enriched(data.dossier, self.growth_signals),
            "sector_relevance": self.sector_relevance(data.verdict),
            "outreach_feasibility": self.outreach_feasibility(data.verdict, checks),
        }
        axes = [
            ScoreAxis(axis=name, value=value, weight=weights[name], reason=reason)
            for name, (value, reason) in values.items()
        ]
        assessed = [(a.value, a.weight) for a in axes if a.value is not None]
        assessed_weight = sum(w for _, w in assessed)
        total = (
            round(sum(v * w for v, w in assessed) / assessed_weight, 1) if assessed_weight else None
        )
        blockers = [c.signal for c in checks["disqualifying"] if c.present == "yes"]
        return ItemScore(total=total, axes=axes, blockers=blockers)

    @staticmethod
    def _enriched(
        dossier: CompanyDossier | None, axis: Callable[[CompanyDossier], AxisValue]
    ) -> AxisValue:
        return NOT_ENRICHED if dossier is None else axis(dossier)

    @staticmethod
    def data_completeness(
        dossier: CompanyDossier | None, fields: dict[str, FieldValue], spec: FieldSpec | None
    ) -> AxisValue:
        required = [f.id for f in spec.fields if f.required] if spec else []
        filled = sum(1 for fid in required if fid in fields)
        if dossier is None:
            if not required:
                return NOT_ENRICHED
            reason = f"{filled}/{len(required)} required fields, not enriched yet"
            return AxisValue(round(100 * filled / len(required), 1), reason)
        present = sum(getattr(dossier, s) is not None for s in ENRICHMENT_SECTIONS)
        sections_part = present / len(ENRICHMENT_SECTIONS)
        if not required:
            return AxisValue(round(100 * sections_part, 1), f"{present}/5 enrichment sections")
        value = 100 * (0.6 * sections_part + 0.4 * filled / len(required))
        reason = f"{present}/5 enrichment sections, {filled}/{len(required)} required fields"
        return AxisValue(round(value, 1), reason)

    @staticmethod
    def review_reputation(dossier: CompanyDossier) -> AxisValue:
        if dossier.reputation is None:
            return AxisValue(NO_DATA, "no reputation data")
        rated = [
            (name, review.rating, review.count)
            for name, review in dossier.reputation.reviews
            if review.rating is not None and review.count > 0
        ]
        if not rated:
            return AxisValue(NO_DATA, "no rated reviews found")
        count = sum(c for _, _, c in rated)
        average = sum(r * c for _, r, c in rated) / count
        # Legacy map: 5.0 -> 100, 4.0 -> 80, 3.0 -> 60, below 3 -> 40.
        value = 40.0 if average < 3 else 60 + (average - 3) * 20
        platforms = ", ".join(name for name, _, _ in rated)
        return AxisValue(
            round(value, 1), f"{average:.1f} average over {count} reviews ({platforms})"
        )

    @staticmethod
    def contact_availability(dossier: CompanyDossier) -> AxisValue:
        contacts = dossier.contacts
        if contacts is None:
            return AxisValue(0.0, "no contact data")
        # Every key person carries a source page, so a named person counts even without LinkedIn.
        named = contacts.key_people[:2]
        linked = sum(1 for p in named if p.linkedin)
        emails = contacts.emails + [p.email for p in contacts.key_people if p.email]
        published = any(e.kind == "published" for e in emails)
        guessed = not published and any(e.kind == "pattern_guess" for e in emails)
        social = contacts.social.linkedin_company or contacts.social.twitter
        parts = [
            (25.0 * len(named) + 5.0 * linked, f"{len(named)} named people, {linked} on LinkedIn"),
            (
                20.0 if published else 5.0 if guessed else 0.0,
                "published email" if published else "guessed email only" if guessed else "no email",
            ),
            (10.0 if contacts.phones else 0.0, "phone" if contacts.phones else "no phone"),
            (10.0 if social else 0.0, "company social" if social else "no social"),
        ]
        return AxisValue(min(100.0, sum(p for p, _ in parts)), ", ".join(r for _, r in parts))

    @staticmethod
    def growth_signals(dossier: CompanyDossier) -> AxisValue:
        growth = dossier.growth
        if growth is None:
            return AxisValue(NO_DATA, "no growth data")
        value = TRAJECTORY_POINTS[growth.trajectory]
        reasons = [growth.trajectory.replace("_", " ")]
        if growth.job_postings.total_open > 10:
            value += 10
            reasons.append(f"{growth.job_postings.total_open} open roles")
        if growth.funding and growth.funding.last_round:
            value += 10
            reasons.append(f"funding: {growth.funding.last_round}")
        return AxisValue(min(100.0, value), ", ".join(reasons))

    @staticmethod
    def sector_relevance(verdict: FitVerdict | None) -> AxisValue:
        if verdict is None:
            return AxisValue(NOT_JUDGED, "fit not judged")
        reason = f"{verdict.sector_match}: {verdict.sector_reason}"
        return AxisValue(SECTOR_POINTS[verdict.sector_match], reason)

    @staticmethod
    def outreach_feasibility(
        verdict: FitVerdict | None, checks: dict[SignalKind, list[SignalCheck]]
    ) -> AxisValue:
        if verdict is None:
            return AxisValue(NOT_JUDGED, "fit not judged")
        hits = [c.signal for c in checks["disqualifying"] if c.present == "yes"]
        if hits:
            return AxisValue(0.0, f"disqualified: {'; '.join(hits)}")
        must, strong = checks["must_have"], checks["strong_positive"]
        met = sum(c.present == "yes" for c in must)
        unknown = sum(c.present == "unknown" for c in must)
        failed = [c.signal for c in must if c.present == "no"]
        # No must-haves defined means nothing to fail, so that part counts as fully met.
        must_part = (met + 0.5 * unknown) / len(must) if must else 1.0
        strong_hits = sum(c.present == "yes" for c in strong)
        strong_part = strong_hits / len(strong) if strong else 0.0
        value = 70 * must_part + 30 * strong_part
        reason = (
            f"{met}/{len(must)} must-haves met, {unknown} unknown, "
            f"{strong_hits}/{len(strong)} strong positives"
        )
        if failed:
            value = min(value, FAILED_MUST_HAVE_CAP)
            reason += f"; fails: {'; '.join(failed)}"
        return AxisValue(round(value, 1), reason)

    @staticmethod
    def _checks(
        verdict: FitVerdict | None, signals: SearchSignals
    ) -> dict[SignalKind, list[SignalCheck]]:
        """Align the judge's answers with the strategy; signals it skipped count as unknown."""
        answered = {(c.kind, c.signal): c for c in verdict.signals} if verdict else {}
        kinds: tuple[SignalKind, ...] = (
            "must_have",
            "strong_positive",
            "weak_positive",
            "disqualifying",
        )
        return {
            kind: [
                answered.get(
                    (kind, signal),
                    SignalCheck(
                        signal=signal, kind=kind, present="unknown", evidence=None, source_url=None
                    ),
                )
                for signal in getattr(signals, kind)
            ]
            for kind in kinds
        }
