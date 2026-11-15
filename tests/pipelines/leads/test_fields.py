from mozg_outreach.domain import FieldDefinition, FieldSpec, FieldType
from mozg_outreach.pipelines.leads.fields import field_task
from mozg_outreach.pipelines.leads.identify import lead_input
from tests.pipelines.seeker.conftest import listing

from .conftest import company, identity

SPEC = FieldSpec(
    fields=[FieldDefinition(id="visa", label="Visa", type=FieldType.BOOLEAN, description="d")]
)


class TestFieldTask:
    def test_confirmed_company_supplies_the_subject(self) -> None:
        lead = lead_input(listing(), "job", "long text " * 400)
        task = field_task(7, lead, identity(company()), SPEC)

        subject = task.request.subject
        assert (subject.name, subject.domain, subject.sector) == (
            "Acme GmbH",
            "acme.example",
            "Logistics software",
        )
        assert task.request.listing is not None
        assert len(task.request.listing.summary) <= 1500, "summary capped like identification"

    def test_unconfirmed_company_falls_back_to_the_listed_name(self) -> None:
        lead = lead_input(listing(company="Acme"), "job", "scope")
        task = field_task(7, lead, identity(), SPEC)

        assert task.request.subject.model_dump() == {
            "name": "Acme",
            "domain": None,
            "location": None,
            "sector": None,
            "description": None,
        }

    def test_client_hidden_behind_a_board_has_no_name(self) -> None:
        lead = lead_input(listing(company="Upwork"), "freelance", "scope", frozenset({"upwork"}))

        assert field_task(7, lead, None, SPEC).request.subject.name is None
