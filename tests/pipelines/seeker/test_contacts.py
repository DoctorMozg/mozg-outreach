from pathlib import Path

import pytest

from mozg_outreach.agents import PromptLibrary
from mozg_outreach.pipelines.biz.enrichment_models import EmailContact, SiteContacts
from mozg_outreach.pipelines.biz.profile_search import ProfileHit
from mozg_outreach.pipelines.leads.models import IdentifiedCompany, LeadIdentity, SourcedFact
from mozg_outreach.pipelines.seeker.contacts import JobContactFinder, employer_domain
from mozg_outreach.pipelines.seeker.models import ContactInput, JobContacts, NamedContact
from mozg_outreach.pipelines.steps import PipelineSteps
from tests.pipelines.biz.conftest import FakeProfiles, FakeSites

from ..conftest import REPO_PROMPTS, RecordedRunner
from .conftest import listing

BOARD_URL = "https://remotive.com/remote-jobs/software-dev/backend-123"
ACME_SITE = SiteContacts(
    emails=[
        EmailContact(
            address="info@acme.example", kind="published", source_url="https://acme.example/contact"
        ),
        EmailContact(
            address="talent@acme.example",
            kind="published",
            source_url="https://acme.example/careers",
        ),
    ],
    pages=["https://acme.example/contact", "https://acme.example/careers"],
)


def found(**fields: object) -> JobContacts:
    values: dict[str, object] = {
        "named_contact": None,
        "careers_email": None,
        "careers_email_source": None,
        "linkedin_company": None,
        "pattern_guesses": ["talent@acme.example", "jobs@acme.example"],
        "apply_url": None,
        "concerns": [],
    }
    return JobContacts.model_validate(values | fields)


def identity(domain: str) -> LeadIdentity:
    fact = SourcedFact(value=domain, source_url="https://acme.example/about", quote="Acme")
    company = IdentifiedCompany(
        name="Acme",
        source_url="https://acme.example/about",
        quote="Acme",
        domain=fact,
        linkedin=None,
        sector=None,
        location=None,
        description=None,
    )
    return LeadIdentity(company=company, people=[], concerns=[])


@pytest.fixture
def sites() -> FakeSites:
    return FakeSites({"acme.example": ACME_SITE})


def finder(
    tmp_path: Path, agent: JobContacts, sites: FakeSites, profiles: FakeProfiles | None = None
) -> tuple[JobContactFinder, RecordedRunner]:
    runner = RecordedRunner({"job-contact-finder": [agent]})
    steps = PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path)
    return JobContactFinder(steps, sites, profiles), runner


class TestSiteSweep:
    async def test_a_known_domain_is_read_before_the_agent_and_handed_to_it(
        self, tmp_path: Path, sites: FakeSites
    ) -> None:
        job, runner = finder(tmp_path, found(), sites)
        contacts = await job.find(listing(url=BOARD_URL), identity("acme.example"))

        assert sites.read_domains == ["acme.example"]
        [sent] = runner.payloads["job-contact-finder"]
        assert isinstance(sent, ContactInput)
        assert sent.site == ACME_SITE
        assert (contacts.careers_email, contacts.careers_email_source) == (
            "talent@acme.example",
            "https://acme.example/careers",
        )
        assert contacts.company_email == "info@acme.example"
        # A guess the site publishes is no longer a guess.
        assert contacts.pattern_guesses == ["jobs@acme.example"]

    async def test_the_agents_domain_is_read_when_nothing_else_named_one(
        self, tmp_path: Path, sites: FakeSites
    ) -> None:
        job, runner = finder(tmp_path, found(company_domain="https://www.acme.example/"), sites)
        contacts = await job.find(listing(url=BOARD_URL), None)

        [sent] = runner.payloads["job-contact-finder"]
        assert isinstance(sent, ContactInput)
        assert sent.site is None
        assert sites.read_domains == ["acme.example"]
        assert contacts.careers_email == "talent@acme.example"

    async def test_an_agent_found_careers_email_is_kept(
        self, tmp_path: Path, sites: FakeSites
    ) -> None:
        agent = found(
            careers_email="hr@acme.example", careers_email_source="https://acme.example/jobs"
        )
        job, _ = finder(tmp_path, agent, sites)
        contacts = await job.find(listing(url=BOARD_URL), identity("acme.example"))
        assert contacts.careers_email == "hr@acme.example"

    @pytest.mark.parametrize(
        "domain", ["boards.greenhouse.io", "jobs.lever.co", "himalayas.app", "linkedin.com"]
    )
    def test_job_boards_are_never_the_employers_domain(self, domain: str) -> None:
        assert employer_domain(f"https://{domain}/acme/123") is None


async def test_a_named_contact_without_linkedin_is_looked_up(
    tmp_path: Path, sites: FakeSites
) -> None:
    person = NamedContact(
        name="Jane Example", title="Recruiter", linkedin=None, email=None, source_url=BOARD_URL
    )
    hit = ProfileHit(
        url="https://www.linkedin.com/in/jane-example",
        title="Jane Example - Recruiter - Acme",
        snippet="",
    )
    profiles = FakeProfiles({"Jane Example": hit})
    job, _ = finder(tmp_path, found(named_contact=person), sites, profiles)
    contacts = await job.find(listing(url=BOARD_URL), None)

    assert profiles.looked_up == [("Jane Example", "Acme GmbH")]
    assert contacts.named_contact is not None
    assert contacts.named_contact.linkedin == hit.url
    assert any("comes from a search result" in c for c in contacts.concerns)
