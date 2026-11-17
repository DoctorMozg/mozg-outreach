"""Who to send a job application to: the employer's own pages first, then the contact finder."""

import logging
import re

from mozg_outreach.pipelines.biz.enrichment import CompanyDomain
from mozg_outreach.pipelines.biz.enrichment_models import SiteContacts
from mozg_outreach.pipelines.biz.profile_search import ProfileFinder, ProfileSearchError
from mozg_outreach.pipelines.biz.site_contacts import SiteReader
from mozg_outreach.pipelines.leads.models import LeadIdentity
from mozg_outreach.pipelines.seeker.models import ContactInput, JobContacts, Listing
from mozg_outreach.pipelines.steps import PipelineSteps

logger = logging.getLogger(__name__)

# Boards and ATS hosts serve many employers; their domain says nothing about this one.
JOB_HOSTS = frozenset(
    {"greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "smartrecruiters.com"}
    | {"himalayas.app", "weworkremotely.com", "workingnomads.com", "remoteok.com", "remotive.com"}
    | {"arbeitnow.com", "ycombinator.com", "wellfound.com", "t.me", "hh.ru", "habr.com"}
)
HR_ADDRESS = re.compile(
    r"^(careers?|jobs?|talent|recruit\w*|hr|people|hiring|join|work|cv|resumes?|apply)\b", re.I
)
INBOX_ADDRESS = re.compile(r"^(info|contact|hello|office|mail|enquir\w*|inquir\w*|team)\b", re.I)


def employer_domain(raw: str | None) -> str | None:
    host = CompanyDomain.usable(raw)
    if host is None or any(host == d or host.endswith(f".{d}") for d in JOB_HOSTS):
        return None
    return host


class JobContactFinder:
    def __init__(
        self,
        steps: PipelineSteps,
        sites: SiteReader | None = None,
        profiles: ProfileFinder | None = None,
    ) -> None:
        self._steps = steps
        self._sites = sites
        self._profiles = profiles

    async def find(self, listing: Listing, identity: LeadIdentity | None) -> JobContacts:
        """Reads the site first when the employer's domain is known, as biz enrichment does."""
        known = identity.company.domain if identity and identity.company else None
        # A posting on the employer's own careers page names its domain too.
        domain = employer_domain(known.value if known else None) or employer_domain(listing.url)
        site = await self._sites.read(domain) if self._sites and domain else None
        contacts = await self._steps.run(
            "seeker",
            "job-contact-finder",
            ContactInput(listing=listing.for_agents([]), site=site),
            JobContacts,
        )
        found = employer_domain(contacts.company_domain)
        if site is None and self._sites and found:
            site = await self._sites.read(found)
        if site is not None:
            add_site(contacts, site)
        await self._link(contacts, listing.company)
        return contacts

    async def _link(self, contacts: JobContacts, company: str) -> None:
        named = contacts.named_contact
        if named is None or named.linkedin or self._profiles is None or not company:
            return
        try:
            hit = await self._profiles.find(named.name, company)
        except ProfileSearchError as exc:
            logger.warning("linkedin search for %s stopped: %s", company, exc)
            contacts.concerns.append(f"LinkedIn search stopped: {exc}")
            return
        if hit is not None:
            named.linkedin = hit.url
            contacts.concerns.append(
                f"LinkedIn for {named.name} comes from a search result; check it: {hit.title}"
            )


def add_site(contacts: JobContacts, site: SiteContacts) -> None:
    """Published HR and general addresses from the employer's pages fill what the agent missed."""
    hr = next((e for e in site.emails if HR_ADDRESS.match(e.address)), None)
    if contacts.careers_email is None and hr is not None:
        contacts.careers_email, contacts.careers_email_source = hr.address, hr.source_url
    inbox = next((e for e in site.emails if INBOX_ADDRESS.match(e.address)), None)
    if contacts.company_email is None and inbox is not None:
        contacts.company_email, contacts.company_email_source = inbox.address, inbox.source_url
    published = {e.address.lower() for e in site.emails}
    contacts.pattern_guesses = [g for g in contacts.pattern_guesses if g.lower() not in published]
    contacts.concerns += site.concerns
