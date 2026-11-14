"""The hard gates every listing passes before any agent sees it."""

import logging
import re
from datetime import date, timedelta

from pydantic import BaseModel

from mozg_outreach.pipelines.job.eligibility import EligibilityChecker
from mozg_outreach.pipelines.job.listings import ListingNormalizer, SourcedListing
from mozg_outreach.pipelines.job.models import (
    Excluded,
    ExcludedReason,
    JobBrief,
    JobStrategy,
    Listing,
)
from mozg_outreach.pipelines.job.skills import SkillMatcher

logger = logging.getLogger(__name__)


class Screening(BaseModel):
    kept: list[Listing]
    excluded: list[Excluded]


class ListingScreener:
    def __init__(self, strategy: JobStrategy, brief: JobBrief, today: date) -> None:
        self._today = today
        self._oldest = today - timedelta(days=brief.recency_days)
        self._eligibility = EligibilityChecker(
            strategy.cv, strategy.preferences, strategy.work_auth_blockers
        )
        self._skills = SkillMatcher(strategy)
        self._blocklist: dict[ExcludedReason, list[str]] = {
            "industry_blocklist": strategy.preferences.excluded_industries,
            "attribute_blocklist": strategy.preferences.excluded_company_attributes,
        }

    def screen(self, scouted: list[SourcedListing]) -> Screening:
        """Recency, blocklists, location, then must-have skills; the first failure excludes."""
        screening = Screening(kept=[], excluded=[])
        for listing in ListingNormalizer(self._today).normalize(scouted):
            outcome = self._check(listing)
            if isinstance(outcome, Excluded):
                screening.excluded.append(outcome)
            else:
                screening.kept.append(outcome)
        logger.info(
            "screening kept %d listings, excluded %d",
            len(screening.kept),
            len(screening.excluded),
        )
        return screening

    def _check(self, listing: Listing) -> Listing | Excluded:
        text = listing_text(listing)
        if listing.posted_on and listing.posted_on < self._oldest:
            return Excluded(listing=listing, reason="stale", detail=f"posted {listing.posted_on}")
        for reason, terms in self._blocklist.items():
            term = next((t for t in terms if t.strip() and _mentions(text, t)), None)
            if term:
                return Excluded(listing=listing, reason=reason, detail=term)
        eligibility = self._eligibility.check(listing)
        listing = listing.model_copy(
            update={
                "eligibility": eligibility.verdict,
                "eligibility_evidence": eligibility.evidence,
            }
        )
        if eligibility.verdict == "no":
            return Excluded(
                listing=listing, reason="location_mismatch", detail=eligibility.evidence
            )
        match = self._skills.match(text)
        if not match.gate_passed:
            missing = [
                *match.missing_must_haves,
                *(" or ".join(g) for g in match.missing_or_groups),
            ]
            detail = f"names none of: {', '.join(missing)}"
            return Excluded(listing=listing, reason="missing_must_have", detail=detail)
        return listing


def listing_text(listing: Listing) -> str:
    parts = (listing.title, listing.company, listing.remote_status_raw, listing.summary)
    return " ".join(p for p in parts if p)


def _mentions(text: str, term: str) -> bool:
    return re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE) is not None
