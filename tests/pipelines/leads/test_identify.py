from pathlib import Path

from mozg_outreach.agents import AgentRunError, PromptLibrary
from mozg_outreach.pipelines.leads.identify import LeadIdentifier, accept_identity, lead_input
from mozg_outreach.pipelines.leads.models import SourcedFact
from mozg_outreach.pipelines.steps import PipelineSteps
from tests.pipelines.conftest import REPO_PROMPTS, RecordedRunner, Scripted
from tests.pipelines.seeker.conftest import listing

from .conftest import CAREERS, company, identity, person


class TestAcceptIdentity:
    def test_keeps_facts_their_pages_show(self) -> None:
        kept = accept_identity(identity(company(), [person()]))

        assert kept.company is not None
        assert kept.company.domain is not None
        assert kept.company.domain.value == "acme.example"
        assert [p.name for p in kept.people] == ["Jane Doe"]
        assert kept.concerns == []

    def test_drops_a_domain_neither_its_page_nor_quote_shows(self) -> None:
        elsewhere = SourcedFact(
            value="acme.io", source_url="https://news.example/acme", quote="Acme raised money"
        )

        kept = accept_identity(identity(company(domain=elsewhere)))

        assert kept.company is not None
        assert kept.company.domain is None
        assert "Dropped domain 'acme.io'" in kept.concerns[0]

    def test_accepts_a_domain_quoted_on_another_page(self) -> None:
        quoted = SourcedFact(
            value="https://www.Acme.io/", source_url=CAREERS, quote="Apply at acme.io/jobs"
        )

        kept = accept_identity(identity(company(domain=quoted)))

        assert kept.company is not None
        assert kept.company.domain is not None
        assert kept.company.domain.value == "acme.io"

    def test_drops_a_linkedin_value_that_is_not_a_page(self) -> None:
        bad = SourcedFact(value="Acme on LinkedIn", source_url=CAREERS, quote="LinkedIn")

        kept = accept_identity(identity(company(linkedin=bad)))

        assert kept.company is not None
        assert kept.company.linkedin is None

    def test_drops_people_the_quote_doesnt_name(self) -> None:
        unnamed = person(name="John Roe", quote="Our hiring team will review applications.")

        kept = accept_identity(identity(None, [unnamed, person()]))

        assert [p.name for p in kept.people] == ["Jane Doe"]
        assert kept.concerns == ["Dropped John Roe: the quote doesn't name them."]

    def test_clears_an_email_missing_from_the_quote(self) -> None:
        kept = accept_identity(identity(None, [person(email="jane@acme.example")]))

        assert kept.people[0].email is None
        assert "email for Jane Doe" in kept.concerns[0]


class TestLeadInput:
    def test_names_the_organization_as_listed(self) -> None:
        lead = lead_input(listing(), "x" * 5000)

        assert lead.organization == "Acme GmbH"
        assert len(lead.summary) == 1500

    def test_a_client_shown_as_the_board_is_hidden(self) -> None:
        gig = listing(company="Malt", sources=["Malt"])

        lead = lead_input(gig, "scope", frozenset({"malt"}))

        assert lead.organization is None

    def test_a_post_without_a_company_starts_from_its_vacancy_link(self) -> None:
        post = listing(company="", apply_url="https://career.habr.com/vacancies/1")

        lead = lead_input(post, "Python developer, remote")

        assert (lead.organization, lead.apply_url) == (None, "https://career.habr.com/vacancies/1")


async def test_a_failed_identification_is_a_concern(tmp_path: Path) -> None:
    script: dict[str, list[Scripted]] = {
        "lead-identifier": [AgentRunError("lead-identifier", "error_max_turns", "out of turns")]
    }
    steps = PipelineSteps(RecordedRunner(script), PromptLibrary(REPO_PROMPTS), tmp_path)

    [outcome] = await LeadIdentifier(steps).identify_many([lead_input(listing(), "s")])

    assert outcome.identity is None
    assert "out of turns" in outcome.concerns[0]
