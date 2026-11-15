from pathlib import Path

import pytest

from mozg_outreach.agents import AgentRunner, PromptLibrary, RunnerSettings
from mozg_outreach.pipelines.biz.models import BizBrief
from mozg_outreach.pipelines.briefs import BriefWriter, FetchLog, page_key
from mozg_outreach.pipelines.seeker.models import SeekerBrief
from mozg_outreach.pipelines.steps import PipelineSteps
from tests.pipelines.conftest import REPO_PROMPTS
from tests.pipelines.seeker.conftest import CV_TEXT

pytestmark = pytest.mark.live


def writer(tmp_path: Path) -> tuple[BriefWriter, FetchLog]:
    fetched = FetchLog()
    runner = AgentRunner(RunnerSettings(default_budget_usd=1.0))
    steps = PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path, fetched)
    return BriefWriter(steps, fetched), fetched


async def test_biz_brief_from_a_company_site(tmp_path: Path) -> None:
    brief_writer, fetched = writer(tmp_path)

    draft = await brief_writer.biz("https://basecamp.com", "")

    assert isinstance(draft.brief, BizBrief)
    assert fetched.pages, "the writer read the site"
    assert draft.brief.offering.what
    assert all(f.source_url and page_key(f.source_url) in fetched.pages for f in draft.brief.facts)
    print(draft.model_dump_json(indent=2))


async def test_seeker_brief_from_a_cv(tmp_path: Path) -> None:
    brief_writer, _ = writer(tmp_path)

    draft = await brief_writer.seeker(CV_TEXT, "", None)

    assert isinstance(draft.brief, SeekerBrief)
    assert draft.brief.preferences
    assert draft.concerns, "a CV without a salary or regions leaves guesses to flag"
    print(draft.model_dump_json(indent=2))
