"""Score judged jobs and gigs and order them best first."""

from datetime import date

from pydantic import BaseModel

from mozg_outreach.domain import ItemScore
from mozg_outreach.pipelines.seeker.judge import Judgments
from mozg_outreach.pipelines.seeker.models import Listing, ListingJudgment
from mozg_outreach.pipelines.seeker.screening import listing_text
from mozg_outreach.pipelines.seeker.skills import SkillMatcher
from mozg_outreach.scoring.seeker import ScoreInput, ScoreProfile, SeekerScorer


class RankedListing(BaseModel):
    listing: Listing
    score: ItemScore
    judgment: ListingJudgment | None


class Ranker:
    def __init__(self, skills: SkillMatcher, profile: ScoreProfile, today: date) -> None:
        self._skills = skills
        self._profile = profile
        self._today = today
        self._scorer = SeekerScorer()

    def rank(self, listings: list[Listing], judgments: Judgments) -> list[RankedListing]:
        ranked = [self._ranked(listing, judgments.by_key.get(listing.key)) for listing in listings]
        return sorted(ranked, key=lambda r: r.score.total or 0.0, reverse=True)

    def _ranked(self, listing: Listing, judgment: ListingJudgment | None) -> RankedListing:
        score = self._scorer.score(
            ScoreInput(
                listing=listing,
                skills=self._skills.match(listing_text(listing)),
                judgment=judgment,
                profile=self._profile,
                today=self._today,
            )
        )
        return RankedListing(listing=listing, score=score, judgment=judgment)
