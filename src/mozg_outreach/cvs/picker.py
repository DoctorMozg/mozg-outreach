"""Profile a CV once, and pick the CV that best answers a listing."""

import logging
import random

from mozg_outreach.cvs.models import (
    PROFILE_CHARS,
    CvChoice,
    CvOption,
    CvPickInput,
    CvProfileDraft,
    CvProfilerInput,
    CvRating,
    CvRatings,
    RankedCv,
)
from mozg_outreach.pipelines.seeker.models import Listing
from mozg_outreach.pipelines.steps import PipelineSteps

logger = logging.getLogger(__name__)

# Points per axis at a full rating of 4; the total runs from 0 to 100.
AXIS_WEIGHTS = {"skills": 40, "title": 25, "seniority": 20, "domain": 15}


class CvProfiler:
    def __init__(self, steps: PipelineSteps) -> None:
        self._steps = steps

    async def profile(self, cv_text: str) -> CvProfileDraft:
        payload = CvProfilerInput(cv_text=cv_text[:PROFILE_CHARS])
        return await self._steps.run("seeker", "cv-profiler", payload, CvProfileDraft)


class CvPicker:
    def __init__(self, steps: PipelineSteps) -> None:
        self._steps = steps

    async def pick(self, listing: Listing, cvs: list[CvOption]) -> CvChoice:
        if not cvs:
            raise ValueError(f"no profiled CV to pick from for {listing.key}")
        if len(cvs) == 1:
            return CvChoice(
                cv_id=cvs[0].id, picked_by="only", listing_language=None, ranked=[], concerns=[]
            )
        # Judges favour what comes first; a seed per listing keeps a rerun's order the same.
        shuffled = random.Random(listing.key).sample(cvs, len(cvs))
        ratings = await self._steps.run(
            "seeker",
            "cv-picker",
            CvPickInput(listing=listing.for_agents([]), cvs=shuffled),
            CvRatings,
        )
        choice = rank(cvs, ratings)
        logger.info("picked CV %d of %d for %s", choice.cv_id, len(cvs), listing.key)
        return choice


def _total(rating: CvRating) -> float:
    points = {
        "skills": rating.skills,
        "title": rating.title,
        "seniority": rating.seniority,
        "domain": rating.domain,
    }
    return sum(AXIS_WEIGHTS[axis] * value / 4 for axis, value in points.items())


def rank(cvs: list[CvOption], ratings: CvRatings) -> CvChoice:
    """CVs in the listing's language first when there are any, then by total, then library order."""
    by_id = {r.cv_id: r for r in ratings.ratings}
    language = ratings.listing_language.lower()
    concerns = list(ratings.concerns)
    matching = {c.id for c in cvs if c.summary.language.lower() == language}
    if not matching:
        concerns.append(f"No CV is written in '{language}', the listing's language.")
    concerns += [f"{c.name} was not rated." for c in cvs if c.id not in by_id]

    def order(position: int) -> tuple[bool, bool, float, int]:
        cv = cvs[position]
        rated = by_id.get(cv.id)
        return (cv.id not in matching, rated is None, -_total(rated) if rated else 0.0, position)

    ranked = [cvs[n] for n in sorted(range(len(cvs)), key=order)]
    return CvChoice(
        cv_id=ranked[0].id,
        picked_by="ratings",
        listing_language=language,
        ranked=[
            RankedCv(
                cv_id=cv.id,
                name=cv.name,
                total=_total(by_id[cv.id]) if cv.id in by_id else None,
                in_listing_language=cv.id in matching,
                reason=by_id[cv.id].reason if cv.id in by_id else None,
            )
            for cv in ranked
        ],
        concerns=concerns,
    )
