"""Deterministic biz lead scoring: six 0-100 axes from agent-extracted features, weighted."""

from collections.abc import Callable

from pydantic import BaseModel, Field

from mozg_outreach.domain import FieldSpec, FieldValue, ItemScore
from mozg_outreach.pipelines.biz.assessment_models import FitVerdict, SignalCheck, SignalKind
from mozg_outreach.pipelines.biz.enrichment_models import CompanyDossier, KeyPerson
from mozg_outreach.pipelines.biz.models import ScoringWeights, SearchSignals
from mozg_outreach.registry.identifiers import looks_personal
from mozg_outreach.scoring.axes import AxisValue, weighted_axes, weighted_total

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


NOT_ENRICHED = AxisValue(None, "not enriched yet")
MAX_SCORED_PEOPLE = 5
UNREACHABLE_POINTS = 3.0
EMAIL_POINTS, LINKEDIN_POINTS, PHONE_POINTS, PERSON_CAP = 35.0, 25.0, 25.0, 45.0


class PersonRoutes(BaseModel):
    """Which ways of reaching one named person were actually found."""

    own_email: bool
    linkedin: bool
    phone: bool

    @classmethod
    def of(cls, person: KeyPerson) -> "PersonRoutes":
        email = person.email
        return cls(
            own_email=bool(
                email and email.kind == "published" and looks_personal(email.address, person.name)
            ),
            linkedin=bool(person.linkedin),
            phone=person.phone is not None,
        )

    @property
    def points(self) -> float:
        total = (
            EMAIL_POINTS * self.own_email
            + LINKEDIN_POINTS * self.linkedin
            + PHONE_POINTS * self.phone
        )
        return min(PERSON_CAP, total)

    def describe(self) -> str:
        found = [
            n
            for n, ok in (
                ("email", self.own_email),
                ("LinkedIn", self.linkedin),
                ("phone", self.phone),
            )
            if ok
        ]
        return "person via " + "+".join(found)


class BizScoreInput(BaseModel):
    dossier: CompanyDossier | None = Field(description="None until the user enriches the item.")
    verdict: FitVerdict | None
    signals: SearchSignals
    fields: dict[str, FieldValue]
    spec: FieldSpec | None
    weights: ScoringWeights


class BizScorer:
    def score(self, data: BizScoreInput) -> ItemScore:
        checks = self._checks(data.verdict, data.signals)
        values = {
            "data_completeness": self.data_completeness(data.dossier, data.fields, data.spec),
            "review_reputation": self._enriched(data.dossier, self.review_reputation),
            "contact_availability": self._enriched(data.dossier, self.contact_availability),
            "growth_signals": self._enriched(data.dossier, self.growth_signals),
            "sector_relevance": self.sector_relevance(data.verdict),
            "outreach_feasibility": self.outreach_feasibility(data.verdict, checks),
        }
        axes = weighted_axes(values, data.weights.model_dump())
        total = weighted_total(axes)
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
        # A name alone is no route, so only email, LinkedIn or a direct phone make a person count.
        routes = [PersonRoutes.of(p) for p in contacts.key_people[:MAX_SCORED_PEOPLE]]
        reachable = sorted((r for r in routes if r.points), key=lambda r: -r.points)[:2]
        unreachable = len(routes) - len([r for r in routes if r.points])
        people_points = sum(r.points for r in reachable) + UNREACHABLE_POINTS * min(unreachable, 2)
        office = contacts.emails
        published = any(e.kind == "published" for e in office)
        guessed = not published and any(e.kind == "pattern_guess" for e in office)
        social = contacts.social.linkedin_company or contacts.social.twitter
        reachable_text = ", ".join(r.describe() for r in reachable) or "no reachable person"
        parts = [
            (people_points, f"{reachable_text}; {unreachable} named without a route"),
            (
                20.0 if published else 5.0 if guessed else 0.0,
                "published office email"
                if published
                else "guessed office email only"
                if guessed
                else "no office email",
            ),
            (10.0 if contacts.phones else 0.0, "phone" if contacts.phones else "no phone"),
            (5.0 if social else 0.0, "company social" if social else "no social"),
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
