"""Who to apply to for one company or listing, without a CV or a search run."""

import logging

from pydantic import BaseModel, Field

from mozg_outreach.pipelines.job.listings import canonical_url
from mozg_outreach.pipelines.job.models import ContactInput, JobContacts, Listing
from mozg_outreach.pipelines.steps import PipelineSteps

logger = logging.getLogger(__name__)


class RecruiterTarget(BaseModel):
    company: str = Field(min_length=1)
    role: str | None = None
    listing_url: str | None = Field(default=None, description="A job page, if the user has one.")
    region: str | None = Field(default=None, description="Disambiguates same-name companies.")


class RecruiterLookup:
    def __init__(self, steps: PipelineSteps) -> None:
        self._steps = steps

    async def lookup(self, target: RecruiterTarget) -> JobContacts:
        url = target.listing_url or ""
        listing = Listing(
            key=canonical_url(url) if url else f"company:{target.company.casefold()}",
            title=target.role or "",
            company=target.company,
            location_string=target.region,
            remote_status_raw=None,
            url=url,
            sources=["user"],
            posted_on=None,
            salary=None,
            summary="",
        )
        logger.info("recruiter lookup for %s", target.company)
        return await self._steps.run(
            "job", "job-contact-finder", ContactInput(listing=listing), JobContacts
        )
