from pathlib import Path

import pytest
from pydantic import TypeAdapter

from mozg_outreach.letters.models import (
    AxisScores,
    CandidateScore,
    JudgeResult,
    LetterDraft,
    ReaderStyle,
)
from mozg_outreach.naturalize.models import NaturalizeResult
from mozg_outreach.pipelines.biz.approach_models import ApproachPlan
from mozg_outreach.pipelines.biz.enrichment_models import (
    CompanyDossier,
    ContactsResult,
    GrowthResult,
    NewsResult,
    ReputationResult,
    SiteContacts,
    TechResult,
)
from mozg_outreach.pipelines.biz.models import Strategy
from mozg_outreach.pipelines.biz.profile_search import ProfileHit, ProfileSearchError
from tests.pipelines.conftest import Scripted

FIXTURES = Path(__file__).parent / "fixtures"

# A copywriter letter body for Katana that fits the email budget and cites only evidence.
BODY = (
    "Congrats on the EUR 14M Series B extension in October. Nine open roles across "
    "engineering, sales and customer success usually means the platform team is about to "
    "carry more load than it was sized for.\n\n"
    "We help B2B SaaS teams in that spot keep cloud spend flat while headcount grows: we map "
    "where the bill goes, rightsize what sits idle, and leave your engineers a plan they can "
    "run themselves.\n\n"
    "Worth a short look at how that would work for Katana?"
)
# The recorded plan's follow-up and breakup, both replies in the opener's thread.
FOLLOW_UP = (
    "One more thought. Katana is hiring a Senior Platform Engineer, and that seat often stays "
    "open for months while the work piles up.\n\n"
    "We can carry that platform load in the meantime, so the new hire joins a calm system "
    "instead of a backlog.\n\n"
    "Could you point me to whoever owns platform hiring?"
)
BREAKUP = (
    "Twice now I've written about taking platform load off your team while Katana grows. The "
    "timing may simply be off, so this is my last note.\n\n"
    "If it becomes useful later, would it be all right to check back next quarter?"
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


def tied_judge(candidates: int) -> JudgeResult:
    """A judge that scores every draft the same, so the first one wins."""
    scores = AxisScores(
        hook_relevance=4,
        specificity=4,
        one_clear_ask=4,
        reader_effort=4,
        sounds_like_sender=4,
        no_ai_tells=4,
    )
    return JudgeResult(
        candidates=[
            CandidateScore(label=label, scores=scores, reason="Even.")
            for label in "ABC"[:candidates]
        ],
        concerns=[],
    )


def outreach_script(candidates: int = 1) -> dict[str, list[Scripted]]:
    """Recorded agents for one Katana enrichment: dossier, approach plan and Jane's letters.

    Without a plan only the first letter is used; the plan's three steps use all of them.
    With several candidates each letter gets that many identical drafts and two tied judges.
    """
    letters = [
        f"Hello Jane,\n\n{body}\n\nBest regards,\nStan" for body in (BODY, FOLLOW_UP, BREAKUP)
    ]
    drafts = [
        LetterDraft(
            subject="Katana's platform load",
            body=BODY,
            hook_used=HOOK,
            angle_used="cost",
            concerns=[],
        ),
        *(
            LetterDraft(subject=None, body=body, hook_used=HOOK, angle_used="cost", concerns=[])
            for body in (FOLLOW_UP, BREAKUP)
        ),
    ]
    judges: list[Scripted] = [tied_judge(candidates)] * (2 * len(drafts))
    return {
        "outreach-scanner": [load_fixture("enrichment/reputation", ReputationResult)],
        "outreach-contact-finder": [load_fixture("enrichment/contacts", ContactsResult)],
        "outreach-news-finder": [load_fixture("enrichment/news", NewsResult)],
        "outreach-growth-analyst": [load_fixture("enrichment/growth", GrowthResult)],
        "outreach-tech-analyst": [load_fixture("enrichment/tech", TechResult)],
        "outreach-approach-planner": [load_fixture("enrichment/approach", ApproachPlan)],
        "outreach-personality-profiler": [STYLE],
        "expert-copywriter": [d for d in drafts for _ in range(candidates)],
        "letter-judge": judges if candidates > 1 else [],
        "expert-naturalizer": [
            NaturalizeResult(text=letter, changes=[], specificity_needed=[], concerns=[])
            for letter in letters
        ],
    }


class FakeProfiles:
    """ProfileFinder fake: a hit per person name, or an error to raise; records the lookups."""

    def __init__(
        self, hits: dict[str, ProfileHit] | None = None, error: ProfileSearchError | None = None
    ) -> None:
        self.hits = hits or {}
        self.error = error
        self.looked_up: list[tuple[str, str]] = []

    async def find(self, name: str, company: str) -> ProfileHit | None:
        self.looked_up.append((name, company))
        if self.error is not None:
            raise self.error
        return self.hits.get(name)


class FakeSites:
    """SiteReader fake: what each domain's own pages publish, and which domains were read."""

    def __init__(self, sites: dict[str, SiteContacts] | None = None) -> None:
        self.sites = sites or {}
        self.read_domains: list[str] = []

    async def read(self, domain: str) -> SiteContacts:
        self.read_domains.append(domain)
        return self.sites.get(domain, SiteContacts())
