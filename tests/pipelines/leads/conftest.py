from mozg_outreach.pipelines.leads.models import (
    IdentifiedCompany,
    LeadIdentity,
    NamedPerson,
    SourcedFact,
)

CAREERS = "https://acme.example/careers"
POSTING = "https://jobs.acme.example/backend"


def company(**fields: object) -> IdentifiedCompany:
    values: dict[str, object] = {
        "name": "Acme GmbH",
        "source_url": CAREERS,
        "quote": "Acme GmbH is hiring a Senior Backend Engineer.",
        "domain": SourcedFact(value="acme.example", source_url=CAREERS, quote="Join Acme"),
        "linkedin": SourcedFact(
            value="https://www.linkedin.com/company/acme-gmbh",
            source_url=CAREERS,
            quote="Follow us on LinkedIn",
        ),
        "sector": SourcedFact(value="Logistics software", source_url=CAREERS, quote="We ship"),
        "location": None,
        "description": None,
    }
    return IdentifiedCompany.model_validate(values | fields)


def person(**fields: object) -> NamedPerson:
    values: dict[str, object] = {
        "name": "Jane Doe",
        "title": "Head of Platform",
        "role": "hiring_manager",
        "linkedin": None,
        "email": None,
        "source_url": POSTING,
        "quote": "You will report to Jane Doe, Head of Platform.",
    }
    return NamedPerson.model_validate(values | fields)


def identity(
    found: IdentifiedCompany | None = None, people: list[NamedPerson] | None = None
) -> LeadIdentity:
    return LeadIdentity(company=found, people=people or [], concerns=[])
