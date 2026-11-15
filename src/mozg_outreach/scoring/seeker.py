"""Deterministic job and gig scoring: 0-100 axes from Python checks and the judge's reads."""

from datetime import date

from pydantic import BaseModel

from mozg_outreach.domain import ItemScore
from mozg_outreach.pipelines.seeker.models import (
    Discipline,
    EngagementType,
    Listing,
    ListingJudgment,
    Money,
    ScoringWeights,
    Seniority,
)
from mozg_outreach.pipelines.seeker.pay import hourly_usd
from mozg_outreach.pipelines.seeker.skills import SkillMatch
from mozg_outreach.scoring.axes import AxisValue, weighted_axes, weighted_total

LADDER: tuple[Seniority, ...] = (
    "intern",
    "junior",
    "mid",
    "senior",
    "staff",
    "principal",
    "executive",
)
COUNTERPARTY_POINTS = {
    "strong": 95.0,
    "established": 80.0,
    "small_or_unknown": 60.0,
    "thin": 40.0,
    "red_flags": 10.0,
}
SCOPE_POINTS = {"clear": 100.0, "partial": 60.0, "vague": 20.0}
NEUTRAL = 70.0
UNKNOWN = 50.0
DEAL_BREAKER_CAP = 40.0
NO_SKILL_POINTS = 15.0
NOT_JUDGED = AxisValue(None, "not judged yet")


class ScoreProfile(BaseModel):
    """What scoring needs from a strategy, for one listing kind."""

    seniority: Seniority
    weights: ScoringWeights
    pay_floor: Money | None
    engagement_types: list[EngagementType] | None = None
    discipline: Discipline | None = None
    secondary_disciplines: list[Discipline] = []


class ScoreInput(BaseModel):
    listing: Listing
    skills: SkillMatch
    judgment: ListingJudgment | None
    profile: ScoreProfile
    today: date


class SeekerScorer:
    def score(self, data: ScoreInput) -> ItemScore:
        judgment, profile = data.judgment, data.profile
        values = {
            "skill_match": self.skill_match(data.skills),
            "seniority_fit": self.seniority_fit(profile.seniority, judgment),
            "location_eligibility": self.location(data.listing),
            "pay_fit": self.pay_fit(data.listing, profile.pay_floor),
            "recency": self.recency(data.listing, data.today),
            "counterparty": self.counterparty(judgment),
            "scope_clarity": self.scope_clarity(judgment),
            "discipline_fit": self.discipline_fit(judgment, profile),
        }
        axes = weighted_axes(values, profile.weights.model_dump())
        total = weighted_total(axes)
        blockers = self.blockers(judgment, profile)
        if blockers and total is not None:
            # A deal-breaker the user named outranks any amount of fit.
            total = min(total, DEAL_BREAKER_CAP)
        return ItemScore(total=total, axes=axes, blockers=blockers)

    @staticmethod
    def blockers(judgment: ListingJudgment | None, profile: ScoreProfile) -> list[str]:
        if judgment is None:
            return []
        blockers = list(judgment.deal_breaker_hits)
        kind, wanted = judgment.engagement_type, profile.engagement_types
        if kind and wanted is not None and kind not in wanted:
            blockers.append(f"engagement type {kind} is not one the user wants")
        return blockers

    @staticmethod
    def skill_match(skills: SkillMatch) -> AxisValue:
        nice = len(skills.nice_to_haves)
        required = [*skills.must_haves, *skills.or_groups]
        named = ", ".join(h.skill for h in [*required, *skills.nice_to_haves]) or "none"
        if required and not any(h.canonical for h in required) and nice == 0:
            return AxisValue(40.0, f"must-haves only through synonyms; matched: {named}")
        if not skills.complete:
            missing = [
                *skills.missing_must_haves,
                *(" or ".join(g) for g in skills.missing_or_groups),
            ]
            # Matching no required skill at all must not land near the middle of the scale.
            points = (55.0 if nice >= 2 else 45.0) if required else NO_SKILL_POINTS
            return AxisValue(points, f"matched: {named}; missing: {', '.join(missing)}")
        points = 95.0 if nice >= 3 else 80.0 if nice >= 1 else 60.0
        if not required:
            points -= 15.0
        return AxisValue(points, f"matched: {named}")

    @staticmethod
    def seniority_fit(cv_level: Seniority, judgment: ListingJudgment | None) -> AxisValue:
        if judgment is None:
            return NOT_JUDGED
        asked = judgment.seniority
        if asked is None:
            return AxisValue(NEUTRAL, "level not stated")
        gap = LADDER.index(asked) - LADDER.index(cv_level)
        points = {0: 100.0, -1: 80.0, 1: 70.0}.get(gap, 50.0 if abs(gap) == 2 else 0.0)
        return AxisValue(points, f"asks {asked}, CV is {cv_level}")

    @staticmethod
    def location(listing: Listing) -> AxisValue:
        points = {"yes": 100.0, "unknown": UNKNOWN, "no": 0.0}[listing.eligibility]
        return AxisValue(points, listing.eligibility_evidence or listing.eligibility)

    @staticmethod
    def pay_fit(listing: Listing, floor_money: Money | None) -> AxisValue:
        floor = hourly_usd(floor_money) if floor_money else None
        if floor is None:
            # Without a floor the axis is left out, and its weight spreads over the rest.
            return AxisValue(None, "no pay floor set")
        pay = listing.pay
        if pay is None or pay.hourly_min_usd is None or pay.hourly_max_usd is None:
            return AxisValue(UNKNOWN, "pay not stated or currency unknown")
        low, high = pay.hourly_min_usd, pay.hourly_max_usd
        band = f"${low:.0f}-${high:.0f}/h ({pay.basis} basis) vs floor ${floor:.0f}/h"
        return band_fit(low, high, floor, band)

    @staticmethod
    def recency(listing: Listing, today: date) -> AxisValue:
        if listing.posted_on is None:
            return AxisValue(UNKNOWN, "posting date unknown")
        days = (today - listing.posted_on).days
        points = 100.0 if days <= 3 else 85.0 if days <= 7 else 70.0 if days <= 14 else 55.0
        return AxisValue(points, f"posted {days} days ago")

    @staticmethod
    def counterparty(judgment: ListingJudgment | None) -> AxisValue:
        if judgment is None:
            return NOT_JUDGED
        points = COUNTERPARTY_POINTS[judgment.counterparty]
        return AxisValue(points, judgment.counterparty_evidence)

    @staticmethod
    def scope_clarity(judgment: ListingJudgment | None) -> AxisValue:
        if judgment is None:
            return NOT_JUDGED
        return AxisValue(SCOPE_POINTS[judgment.scope_clarity], judgment.scope_evidence)

    @staticmethod
    def discipline_fit(judgment: ListingJudgment | None, profile: ScoreProfile) -> AxisValue:
        if judgment is None:
            return NOT_JUDGED
        if profile.discipline is None:
            return AxisValue(None, "no discipline in the strategy")
        found = judgment.discipline
        if found is None:
            return AxisValue(UNKNOWN, "discipline unclear")
        if found == profile.discipline:
            return AxisValue(100.0, f"{found}, the CV's main discipline")
        if found in profile.secondary_disciplines:
            return AxisValue(70.0, f"{found}, a secondary discipline")
        return AxisValue(20.0, f"{found}, outside the CV's disciplines")


def band_fit(low: float, high: float, floor: float, band: str) -> AxisValue:
    """A pay band against the user's floor: the top must clear it, the bottom decides how well."""
    if high < floor * 0.70:
        return AxisValue(0.0, f"far below floor: {band}")
    if high < floor:
        return AxisValue(30.0, f"below floor: {band}")
    if high >= floor * 1.10 and low >= floor * 0.90:
        return AxisValue(100.0, band)
    if low >= floor * 0.90:
        return AxisValue(85.0, band)
    if low < floor * 0.75:
        return AxisValue(50.0, f"bottom of band well below floor: {band}")
    return AxisValue(70.0, band)
