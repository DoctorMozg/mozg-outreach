from pathlib import Path

from mozg_outreach.agents import PromptLibrary
from mozg_outreach.letters.composer import WrittenLetter
from mozg_outreach.letters.models import LetterBrief, LetterDraft, SenderSettings
from mozg_outreach.naturalize.models import NaturalizeResult
from mozg_outreach.naturalize.naturalizer import Naturalizer
from mozg_outreach.pipelines.seeker.models import SeekerStrategy
from mozg_outreach.pipelines.seeker.pay import PayParser
from mozg_outreach.pipelines.seeker.proposals import ProposalRequest, ProposalWriter
from mozg_outreach.pipelines.seeker.skills import SkillMatcher
from mozg_outreach.pipelines.steps import PipelineSteps

from ..conftest import REPO_PROMPTS, RecordedRunner
from .conftest import CV_TEXT, GIG_URL, SCOPE, gig


async def test_proposal_quotes_scope_and_only_supplied_prices(
    tmp_path: Path, strategy: SeekerStrategy
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
    hidden = gig(PayParser().parse("EUR 700/day", "gig"), company="Malt")
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
    assert {"Posted budget: EUR 700/day", "Rate: EUR 90 per hour"} <= set(brief.verified_entities)
    assert brief.hooks[0].signal.startswith("We need a backend engineer")
