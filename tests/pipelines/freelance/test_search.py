from datetime import date
from pathlib import Path

from mozg_outreach.agents import PromptLibrary
from mozg_outreach.letters.composer import WrittenLetter
from mozg_outreach.letters.models import LetterBrief, LetterDraft, SenderSettings
from mozg_outreach.naturalize.models import NaturalizeResult
from mozg_outreach.naturalize.naturalizer import Naturalizer
from mozg_outreach.pipelines.freelance.budget import BudgetParser
from mozg_outreach.pipelines.freelance.models import FreelanceBrief, FreelanceStrategy
from mozg_outreach.pipelines.freelance.proposals import ProposalRequest, ProposalWriter
from mozg_outreach.pipelines.freelance.search import FreelanceSearch, load_catalog
from mozg_outreach.pipelines.job.models import SourcePlan
from mozg_outreach.pipelines.job.skills import SkillMatcher
from mozg_outreach.pipelines.steps import PipelineSteps
from tests.pipelines.conftest import REPO_PROMPTS, RecordedRunner
from tests.pipelines.job.conftest import CV_TEXT

from .conftest import GIG_URL, SCOPE, gig, scouted_gig

CATALOG = REPO_PROMPTS / "job" / "sources" / "freelance_canonical.json"
TODAY = date(2026, 11, 14)


def search(runner: RecordedRunner, tmp_path: Path) -> FreelanceSearch:
    steps = PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path)
    return FreelanceSearch(steps, load_catalog(CATALOG))


async def test_sources_follow_the_discipline(tmp_path: Path, strategy: FreelanceStrategy) -> None:
    runner = RecordedRunner({"job-source-researcher": [SourcePlan(augmented=[], concerns=[])]})
    finder = search(runner, tmp_path)
    names = {s.name for s in await finder.sources(strategy)}
    assert {"Gun.io", "Malt"} <= names
    assert "Dribbble Jobs" not in names
    assert all(not s.scrape_allowed for s in finder.guidance(strategy))


def test_screening_joins_terms_back_and_applies_gates(
    tmp_path: Path, strategy: FreelanceStrategy, brief: FreelanceBrief
) -> None:
    screening = search(RecordedRunner({}), tmp_path).screen(
        [
            scouted_gig(),
            scouted_gig(url="https://malt.example/2", client="BadCorp"),
            scouted_gig(
                url="https://malt.example/3", title="Landing page", scope_raw="Figma redesign."
            ),
        ],
        strategy,
        brief,
        TODAY,
    )
    [kept] = screening.kept
    assert kept.listing.key == GIG_URL
    assert kept.terms.budget is not None
    assert kept.terms.budget.basis == "day"
    assert sorted(e.reason for e in screening.excluded) == [
        "attribute_blocklist",
        "missing_must_have",
    ]


async def test_proposal_quotes_scope_and_only_supplied_prices(
    tmp_path: Path, strategy: FreelanceStrategy
) -> None:
    body = " ".join(["word"] * 300)
    framed = f"Hi there,\n\n{body}\n\nThanks,\nStan"
    runner = RecordedRunner(
        {
            "expert-copywriter": [
                LetterDraft(subject=None, body=body, hook_used="", angle_used="", concerns=[])
            ],
            "expert-naturalizer": [
                NaturalizeResult(text=framed, changes=[], specificity_needed=[], concerns=[])
            ],
        }
    )
    steps = PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path)
    hidden = gig(BudgetParser().parse("EUR 700/day", None), company="Malt")
    letter = await ProposalWriter(steps, Naturalizer(steps)).write(
        ProposalRequest(
            gig=hidden,
            skills=SkillMatcher(strategy).match(SCOPE),
            rate=strategy.preferences.rate_floor,
            cv_text=CV_TEXT,
            sender=SenderSettings(name="Stan Petrov", tone="warm"),
        )
    )
    assert isinstance(letter, WrittenLetter)
    assert letter.text.startswith("Hi there,")
    assert letter.review.address == GIG_URL
    [brief] = runner.payloads["expert-copywriter"]
    assert isinstance(brief, LetterBrief)
    assert brief.channel == "proposal"
    assert {"Posted budget: EUR 700/day", "Rate: EUR 80 per hour"} <= set(brief.verified_entities)
    assert brief.hooks[0].signal.startswith("We need a backend engineer")
