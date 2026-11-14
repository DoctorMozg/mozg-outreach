from pathlib import Path

import pytest
from pydantic import TypeAdapter

from mozg_outreach.letters.models import ReaderStyle
from mozg_outreach.pipelines.biz.enrichment_models import (
    CompanyDossier,
    ContactsResult,
    GrowthResult,
    NewsResult,
    TechResult,
)
from mozg_outreach.pipelines.biz.models import Strategy

FIXTURES = Path(__file__).parent / "fixtures"

# A copywriter letter body for Katana that fits the email budget and cites only evidence.
BODY = (
    "Congrats on the EUR 14M Series B extension in October. Nine open roles across "
    "engineering, sales and customer success usually means the platform team is about to "
    "carry a lot more load than it was sized for.\n\n"
    "We help B2B SaaS teams in that spot keep cloud spend flat while headcount grows. The work "
    "is fixed in scope: we map where the cloud bill goes, rightsize what is idle, and leave your "
    "engineers a plan they can run themselves. Nothing in the product changes, and releases keep "
    "their usual rhythm while we do it.\n\n"
    "You are also hiring a Senior Platform Engineer, which is often the moment teams decide "
    "whether to keep tuning by hand or put guardrails in place first. We can help with either "
    "path, and I am happy to share how other teams handled it.\n\n"
    "Would a 20-minute call next week be useful to compare notes on your setup?"
)
HOOK = "Katana secures EUR 14M Series B extension (2025-10)"
STYLE = ReaderStyle(
    social_style=None,
    role_priorities=["technical fit", "reliability"],
    confidence="none",
    calibration_directive=["Lead with the technical fit."],
    evidence=[],
    concerns=[],
)


def load_fixture[TModel](name: str, model: type[TModel]) -> TModel:
    """Validate `fixtures/<name>.json` (name may include a subdirectory) against `model`."""
    return TypeAdapter(model).validate_json((FIXTURES / f"{name}.json").read_text())


@pytest.fixture(scope="session")
def strategy() -> Strategy:
    return load_fixture("strategy", Strategy)


@pytest.fixture
def dossier() -> CompanyDossier:
    """Katana's recorded enrichment; Jane Example has no email or LinkedIn of her own."""
    return CompanyDossier(
        contacts=load_fixture("enrichment/contacts", ContactsResult),
        news=load_fixture("enrichment/news", NewsResult),
        growth=load_fixture("enrichment/growth", GrowthResult),
        tech=load_fixture("enrichment/tech", TechResult),
    )
