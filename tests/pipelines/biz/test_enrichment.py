from pathlib import Path

import pytest
from pydantic import BaseModel

from mozg_outreach.agents import AgentRunError, PromptLibrary
from mozg_outreach.domain import FieldValue, ItemCandidate, ItemKind
from mozg_outreach.pipelines.biz.enrichment import (
    BizEnrichment,
    CompanyDomain,
    brief_from_candidate,
)
from mozg_outreach.pipelines.biz.enrichment_models import (
    CompanyBrief,
    CompanyDossier,
    CompanyWebsite,
    ContactBrief,
    ContactsResult,
    EmailContact,
    GrowthResult,
    NewsResult,
    ProfileLink,
    ReputationResult,
    SiteContacts,
    TechResult,
)
from mozg_outreach.pipelines.biz.models import Strategy
from mozg_outreach.pipelines.biz.profile_search import ProfileHit, ProfileSearchError
from mozg_outreach.pipelines.steps import PipelineSteps
from tests.pipelines.biz.conftest import FakeProfiles, FakeSites, load_fixture
from tests.pipelines.conftest import REPO_PROMPTS, RecordedRunner, Scripted

PRIVACY = "https://katanamrp.com/privacy"


def load[TModel: BaseModel](name: str, model: type[TModel]) -> TModel:
    return load_fixture(f"enrichment/{name}", model)


@pytest.fixture(scope="module")
def company(strategy: Strategy) -> CompanyBrief:
    return CompanyBrief(
        name="Katana",
        domain="katanamrp.com",
        location="Tallinn, Estonia",
        sector="B2B SaaS",
        target_profile=strategy.target_profile,
        outreach_angles=strategy.outreach_angles,
    )


@pytest.fixture
def script() -> dict[str, list[Scripted]]:
    return {
        "outreach-scanner": [load("reputation", ReputationResult)],
        "outreach-contact-finder": [load("contacts", ContactsResult)],
        "outreach-news-finder": [load("news", NewsResult)],
        "outreach-growth-analyst": [load("growth", GrowthResult)],
        "outreach-tech-analyst": [load("tech", TechResult)],
    }


def enrichment(runner: RecordedRunner, tmp_path: Path) -> BizEnrichment:
    return BizEnrichment(PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path))


async def test_full_dossier(
    tmp_path: Path, company: CompanyBrief, script: dict[str, list[Scripted]]
) -> None:
    runner = RecordedRunner(script)
    steps = PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path)
    e = BizEnrichment(steps)

    dossier = await e.enrich(company)

    assert dossier.concerns == []
    assert dossier.reputation is not None
    assert dossier.reputation.reviews.glassdoor.sentiment == "positive"
    assert dossier.news is not None
    assert dossier.news.items[0].url.startswith("https://estonia.ee/")
    assert dossier.growth is not None
    assert dossier.growth.trajectory == "rapid_growth"
    assert dossier.tech is not None
    assert dossier.tech.maturity == "mid"
    assert steps.cost_usd == pytest.approx(0.5)
    others = {k: v for k, v in runner.payloads.items() if k != "outreach-contact-finder"}
    assert all(payloads == [company] for payloads in others.values())
    [contact_input] = runner.payloads["outreach-contact-finder"]
    assert contact_input == ContactBrief(**company.model_dump(), site=None)


class TestSite:
    async def test_the_agent_starts_from_the_site_and_its_addresses_are_kept(
        self, tmp_path: Path, company: CompanyBrief, script: dict[str, list[Scripted]]
    ) -> None:
        site = SiteContacts(
            emails=[
                EmailContact(address="HELLO@katanamrp.com", kind="published", source_url="u"),
                EmailContact(address="dpo@katanamrp.com", kind="published", source_url=PRIVACY),
            ],
            contact_form="https://katanamrp.com/contact",
            concerns=["one page was slow"],
        )
        sites = FakeSites({"katanamrp.com": site})
        runner = RecordedRunner(script)
        steps = PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path)

        dossier = await BizEnrichment(steps, sites).enrich(company)

        [contact_input] = runner.payloads["outreach-contact-finder"]
        assert isinstance(contact_input, ContactBrief)
        assert contact_input.site == site
        assert dossier.contacts is not None
        # The agent already had hello@ (any case); the privacy page's DPO address is added.
        assert [(e.address, e.source_url) for e in dossier.contacts.emails][-1] == (
            "dpo@katanamrp.com",
            PRIVACY,
        )
        assert sum(e.address.lower() == "hello@katanamrp.com" for e in dossier.contacts.emails) == 1
        assert "one page was slow" in dossier.contacts.concerns
        assert dossier.contacts.contact_form == "https://katanamrp.com/contact"

    async def test_without_a_domain_the_site_the_agent_found_is_read_after(
        self, tmp_path: Path, company: CompanyBrief, script: dict[str, list[Scripted]]
    ) -> None:
        sites = FakeSites()
        steps = PipelineSteps(RecordedRunner(script), PromptLibrary(REPO_PROMPTS), tmp_path)

        await BizEnrichment(steps, sites).enrich(company.model_copy(update={"domain": None}))

        contacts = load("contacts", ContactsResult)
        assert contacts.website is not None
        assert sites.read_domains == [CompanyDomain.usable(contacts.website.url)]


class TestProfiles:
    @pytest.fixture
    def steps(self, tmp_path: Path, script: dict[str, list[Scripted]]) -> PipelineSteps:
        return PipelineSteps(RecordedRunner(script), PromptLibrary(REPO_PROMPTS), tmp_path)

    async def test_the_sites_own_link_is_used_before_any_search(
        self, steps: PipelineSteps, company: CompanyBrief
    ) -> None:
        link = ProfileLink(
            url="https://www.linkedin.com/in/jane-x", text="Jane  Example", source_url="u"
        )
        sites = FakeSites({"katanamrp.com": SiteContacts(profiles=[link])})
        profiles = FakeProfiles()

        dossier = await BizEnrichment(steps, sites, profiles).enrich(company)

        assert dossier.contacts is not None
        assert dossier.contacts.key_people[0].linkedin == "https://www.linkedin.com/in/jane-x"
        assert profiles.looked_up == []

    async def test_a_search_hit_is_linked_and_flagged_for_checking(
        self, steps: PipelineSteps, company: CompanyBrief
    ) -> None:
        hit = ProfileHit(
            url="https://www.linkedin.com/in/jane-e",
            title="Jane Example - CTO - Katana | LinkedIn",
            snippet="",
        )
        profiles = FakeProfiles({"Jane Example": hit})

        dossier = await BizEnrichment(steps, FakeSites(), profiles).enrich(company)

        assert dossier.contacts is not None
        assert dossier.contacts.key_people[0].linkedin == hit.url
        assert profiles.looked_up == [("Jane Example", company.name)]
        assert any("check it: Jane Example - CTO" in c for c in dossier.contacts.concerns)

    async def test_a_failed_search_is_a_concern(
        self, steps: PipelineSteps, company: CompanyBrief
    ) -> None:
        profiles = FakeProfiles(error=ProfileSearchError("Brave Search answered 429"))

        dossier = await BizEnrichment(steps, FakeSites(), profiles).enrich(company)

        assert dossier.contacts is not None
        assert dossier.contacts.key_people[0].linkedin is None
        assert "LinkedIn search stopped: Brave Search answered 429" in dossier.contacts.concerns


async def test_failed_agent_becomes_a_concern(
    tmp_path: Path, company: CompanyBrief, script: dict[str, list[Scripted]]
) -> None:
    script["outreach-news-finder"] = [
        AgentRunError("outreach-news-finder", "error_max_turns", "ran out of turns")
    ]
    dossier = await enrichment(RecordedRunner(script), tmp_path).enrich(company)

    assert dossier.news is None
    assert dossier.contacts is not None
    assert dossier.tech is not None
    assert len(dossier.concerns) == 1
    assert dossier.concerns[0].startswith("News for Katana failed")


async def test_unexpected_error_propagates(
    tmp_path: Path, company: CompanyBrief, script: dict[str, list[Scripted]]
) -> None:
    script["outreach-tech-analyst"] = [RuntimeError("boom")]
    with pytest.raises(RuntimeError, match="boom"):
        await enrichment(RecordedRunner(script), tmp_path).enrich(company)


async def test_enrich_many_keeps_input_order(
    tmp_path: Path, company: CompanyBrief, script: dict[str, list[Scripted]]
) -> None:
    other = company.model_copy(update={"name": "Blackwall"})
    doubled = {agent: results * 2 for agent, results in script.items()}
    dossiers = await enrichment(RecordedRunner(doubled), tmp_path).enrich_many([company, other])
    assert len(dossiers) == 2
    assert all(d.concerns == [] for d in dossiers)


def test_contacts_keep_pattern_guess_kind() -> None:
    contacts = load("contacts", ContactsResult)
    restored = ContactsResult.model_validate_json(contacts.model_dump_json())
    assert [(e.address, e.kind) for e in restored.emails] == [
        ("hello@katanamrp.com", "published"),
        ("partnerships@katanamrp.com", "pattern_guess"),
    ]
    assert all(e.source_url for e in restored.emails)
    assert all(p.source_url for p in restored.key_people)


def test_brief_from_candidate(strategy: Strategy) -> None:
    candidate = ItemCandidate(
        kind=ItemKind.COMPANY,
        dedup_key="katanamrp.com",
        title="Katana",
        fields={
            "domain": FieldValue(value="katanamrp.com"),
            "location": FieldValue(value="Tallinn, Estonia"),
            "source_urls": FieldValue(value=["https://estonia.ee/x"]),
        },
    )
    brief = brief_from_candidate(candidate, strategy)
    assert (brief.name, brief.domain, brief.location) == (
        "Katana",
        "katanamrp.com",
        "Tallinn, Estonia",
    )
    assert brief.sector is None
    assert brief.target_profile == strategy.target_profile
    assert brief.outreach_angles == strategy.outreach_angles


def test_brief_carries_what_search_already_knew(strategy: Strategy) -> None:
    candidate = ItemCandidate(
        kind=ItemKind.COMPANY,
        dedup_key="katana",
        title="Katana",
        fields={
            "decision_maker": FieldValue(value="Jane Example, CTO"),
            "preferred_channel": FieldValue(value="LinkedIn"),
            "recent_trigger": FieldValue(value="Series B in October"),
            "source_urls": FieldValue(value=["https://estonia.ee/x", "https://katanamrp.com"]),
        },
    )
    brief = brief_from_candidate(candidate, strategy)
    assert brief.decision_maker == "Jane Example, CTO"
    assert brief.preferred_channel == "LinkedIn"
    assert brief.recent_trigger == "Series B in October"
    assert brief.source_urls == ["https://estonia.ee/x", "https://katanamrp.com"]


def test_listing_site_domain_is_not_a_company_domain(strategy: Strategy) -> None:
    candidate = ItemCandidate(
        kind=ItemKind.COMPANY,
        dedup_key="katana",
        title="Katana",
        fields={"domain": FieldValue(value="https://www.trustpilot.com/review/katanamrp.com")},
    )
    assert brief_from_candidate(candidate, strategy).domain is None


class TestCompanyDomain:
    @staticmethod
    def with_website(url: str, source: str = "https://katanamrp.com/about/") -> CompanyDossier:
        contacts = load("contacts", ContactsResult)
        contacts.website = CompanyWebsite(url=url, source_url=source, quote="Katana")
        return CompanyDossier(contacts=contacts)

    def test_taken_from_the_contact_finders_website(self) -> None:
        found = CompanyDomain.from_dossier(self.with_website("https://www.katanamrp.com/en"))
        assert found is not None
        assert (found.value, found.source_url) == ("katanamrp.com", "https://katanamrp.com/about/")

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.trustpilot.com/review/katanamrp.com",
            "https://uk.linkedin.com/company/katana",
            "https://chambers.com/firm/katana",
            "https://www.legal500.com/firms/katana",
        ],
    )
    def test_listing_sites_never_qualify(self, url: str) -> None:
        assert CompanyDomain.from_dossier(self.with_website(url)) is None

    def test_website_without_a_source_is_ignored(self) -> None:
        assert CompanyDomain.from_dossier(self.with_website("https://katanamrp.com", "")) is None


async def test_people_without_a_quote_are_dropped(
    tmp_path: Path, company: CompanyBrief, script: dict[str, list[Scripted]]
) -> None:
    contacts = load("contacts", ContactsResult)
    jane = contacts.key_people[0]
    contacts.key_people.append(jane.model_copy(update={"name": "Ghost", "quote": " "}))
    tech = load("tech", TechResult)
    maker = tech.tech_decision_makers[0]
    tech.tech_decision_makers.append(maker.model_copy(update={"name": "Phantom", "quote": ""}))
    script["outreach-contact-finder"] = [contacts]
    script["outreach-tech-analyst"] = [tech]

    dossier = await enrichment(RecordedRunner(script), tmp_path).enrich(company)

    assert dossier.contacts is not None
    assert dossier.tech is not None
    assert [p.name for p in dossier.contacts.key_people] == ["Jane Example"]
    assert [p.name for p in dossier.tech.tech_decision_makers] == ["Jane Example"]
    assert any("Ghost" in c for c in dossier.concerns)
    assert any("Phantom" in c for c in dossier.concerns)
