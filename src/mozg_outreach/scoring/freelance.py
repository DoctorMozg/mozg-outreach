"""Deterministic gig scoring: seven 0-100 axes from Python checks and the judge's reads."""

from datetime import date

from pydantic import BaseModel

from mozg_outreach.domain import ItemScore
from mozg_outreach.pipelines.freelance.budget import hourly_usd
from mozg_outreach.pipelines.freelance.models import FreelanceStrategy, Gig, GigJudgment
from mozg_outreach.pipelines.seeker.skills import SkillMatch
from mozg_outreach.scoring.axes import AxisValue, weighted_axes, weighted_total
from mozg_outreach.scoring.seeker import DEAL_BREAKER_CAP, NOT_JUDGED, UNKNOWN, JobScorer, band_fit

SCOPE_POINTS = {"clear": 100.0, "partial": 60.0, "vague": 20.0}
VETTING_POINTS = {
    "vetted_platform": 90.0,
    "verified_client": 80.0,
    "unverified": 50.0,
    "red_flags": 10.0,
}


class GigScoreInput(BaseModel):
    gig: Gig
    skills: SkillMatch
    judgment: GigJudgment | None
    strategy: FreelanceStrategy
    today: date


class FreelanceScorer:
    def score(self, data: GigScoreInput) -> ItemScore:
        listing, judgment = data.gig.listing, data.judgment
        values = {
            "skill_match": JobScorer.skill_match(data.skills),
            "location_eligibility": JobScorer.location(listing),
            "scope_clarity": self.scope_clarity(judgment),
            "budget_fit": self.budget_fit(data.gig, data.strategy),
            "client_vetting": self.client_vetting(judgment),
            "recency": JobScorer.recency(listing, data.today),
            "discipline_fit": self.discipline_fit(judgment, data.strategy),
        }
        axes = weighted_axes(values, data.strategy.scoring_weights.model_dump())
        total = weighted_total(axes)
        blockers = self.blockers(judgment, data.strategy)
        if blockers and total is not None:
            total = min(total, DEAL_BREAKER_CAP)
        return ItemScore(total=total, axes=axes, blockers=blockers)

    @staticmethod
    def blockers(judgment: GigJudgment | None, strategy: FreelanceStrategy) -> list[str]:
        if judgment is None:
            return []
        blockers = list(judgment.deal_breaker_hits)
        kind = judgment.engagement_type
        if kind and kind not in strategy.preferences.engagement_types:
            blockers.append(f"engagement type {kind} is not one the user wants")
        return blockers

    @staticmethod
    def budget_fit(gig: Gig, strategy: FreelanceStrategy) -> AxisValue:
        floor_money = strategy.preferences.rate_floor
        floor = hourly_usd(floor_money) if floor_money else None
        if floor is None:
            # Without a floor the axis is left out, and its weight spreads over the rest.
            return AxisValue(None, "no rate floor set")
        budget = gig.terms.budget
        if budget is None or budget.hourly_min_usd is None or budget.hourly_max_usd is None:
            return AxisValue(UNKNOWN, "budget not stated or currency unknown")
        low, high = budget.hourly_min_usd, budget.hourly_max_usd
        band = f"${low:.0f}-${high:.0f}/h ({budget.basis} basis) vs floor ${floor:.0f}/h"
        return band_fit(low, high, floor, band)

    @staticmethod
    def scope_clarity(judgment: GigJudgment | None) -> AxisValue:
        if judgment is None:
            return NOT_JUDGED
        return AxisValue(SCOPE_POINTS[judgment.scope_clarity], judgment.scope_evidence)

    @staticmethod
    def client_vetting(judgment: GigJudgment | None) -> AxisValue:
        if judgment is None:
            return NOT_JUDGED
        return AxisValue(VETTING_POINTS[judgment.client_vetting], judgment.vetting_evidence)

    @staticmethod
    def discipline_fit(judgment: GigJudgment | None, strategy: FreelanceStrategy) -> AxisValue:
        if judgment is None:
            return NOT_JUDGED
        found = judgment.discipline
        if found is None:
            return AxisValue(UNKNOWN, "discipline unclear")
        if found == strategy.discipline:
            return AxisValue(100.0, f"{found} gig, the CV's main discipline")
        if found in strategy.secondary_disciplines:
            return AxisValue(70.0, f"{found} gig, a secondary discipline")
        return AxisValue(20.0, f"{found} gig, outside the CV's disciplines")
