"""Deterministic job scoring: six 0-100 axes from Python checks and the judge's reads."""

from datetime import date

from pydantic import BaseModel

from mozg_outreach.domain import ItemScore, ScoreAxis
from mozg_outreach.pipelines.job.models import (
    JobStrategy,
    Listing,
    ListingJudgment,
    Seniority,
)
from mozg_outreach.pipelines.job.salary import annual_usd
from mozg_outreach.pipelines.job.skills import SkillMatch
from mozg_outreach.scoring.biz import AxisValue

LADDER: tuple[Seniority, ...] = (
    "intern",
    "junior",
    "mid",
    "senior",
    "staff",
    "principal",
    "executive",
)
EMPLOYER_POINTS = {
    "strong": 95.0,
    "established": 80.0,
    "small_or_unknown": 60.0,
    "thin": 40.0,
    "red_flags": 10.0,
}
NEUTRAL = 70.0
UNKNOWN = 50.0
DEAL_BREAKER_CAP = 40.0
NOT_JUDGED = AxisValue(None, "not judged yet")


class JobScoreInput(BaseModel):
    listing: Listing
    skills: SkillMatch
    judgment: ListingJudgment | None
    strategy: JobStrategy
    today: date


class JobScorer:
    def score(self, data: JobScoreInput) -> ItemScore:
        weights = data.strategy.scoring_weights.model_dump()
        if sum(weights.values()) <= 0:
            raise ValueError("job scoring weights sum to 0; at least one must be > 0")
        values = {
            "skill_match": self.skill_match(data.skills),
            "seniority_fit": self.seniority_fit(data.strategy.cv.seniority, data.judgment),
            "location_eligibility": self.location(data.listing),
            "salary_fit": self.salary_fit(data.listing, data.strategy),
            "recency": self.recency(data.listing, data.today),
            "employer_signals": self.employer(data.judgment),
        }
        axes = [
            ScoreAxis(axis=name, value=value, weight=weights[name], reason=reason)
            for name, (value, reason) in values.items()
        ]
        assessed = [(a.value, a.weight) for a in axes if a.value is not None]
        weight = sum(w for _, w in assessed)
        total = round(sum(v * w for v, w in assessed) / weight, 1) if weight else None
        blockers = list(data.judgment.deal_breaker_hits) if data.judgment else []
        if blockers and total is not None:
            # A deal-breaker the user named outranks any amount of fit.
            total = min(total, DEAL_BREAKER_CAP)
        return ItemScore(total=total, axes=axes, blockers=blockers)

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
            return AxisValue(
                55.0 if nice >= 2 else 45.0, f"matched: {named}; missing: {', '.join(missing)}"
            )
        points = 95.0 if nice >= 3 else 80.0 if nice >= 1 else 60.0
        if not required:
            points -= 15.0
        return AxisValue(points, f"matched: {named}")

    @staticmethod
    def seniority_fit(cv_level: Seniority, judgment: ListingJudgment | None) -> AxisValue:
        if judgment is None:
            return NOT_JUDGED
        asked = judgment.listing_seniority
        if asked is None:
            return AxisValue(NEUTRAL, "level not stated")
        gap = LADDER.index(asked) - LADDER.index(cv_level)
        points = {0: 100.0, -1: 80.0, 1: 70.0}.get(gap, 50.0 if abs(gap) == 2 else 0.0)
        return AxisValue(points, f"role asks {asked}, CV is {cv_level}")

    @staticmethod
    def location(listing: Listing) -> AxisValue:
        points = {"yes": 100.0, "unknown": UNKNOWN, "no": 0.0}[listing.eligibility]
        return AxisValue(points, listing.eligibility_evidence or listing.eligibility)

    @staticmethod
    def salary_fit(listing: Listing, strategy: JobStrategy) -> AxisValue:
        floor_money = strategy.preferences.salary_floor
        floor = annual_usd(floor_money) if floor_money else None
        salary = listing.salary
        if floor is None:
            return AxisValue(NEUTRAL, "no salary floor set")
        if salary is None or salary.annual_max_usd is None or salary.annual_min_usd is None:
            return AxisValue(NEUTRAL, "salary not disclosed or currency unknown")
        low, high = salary.annual_min_usd, salary.annual_max_usd
        band = f"band ${low / 1000:.0f}k-${high / 1000:.0f}k vs floor ${floor / 1000:.0f}k"
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

    @staticmethod
    def recency(listing: Listing, today: date) -> AxisValue:
        if listing.posted_on is None:
            return AxisValue(UNKNOWN, "posting date unknown")
        days = (today - listing.posted_on).days
        points = 100.0 if days <= 3 else 85.0 if days <= 7 else 70.0 if days <= 14 else 55.0
        return AxisValue(points, f"posted {days} days ago")

    @staticmethod
    def employer(judgment: ListingJudgment | None) -> AxisValue:
        if judgment is None:
            return NOT_JUDGED
        return AxisValue(EMPLOYER_POINTS[judgment.employer_signal], judgment.employer_evidence)
