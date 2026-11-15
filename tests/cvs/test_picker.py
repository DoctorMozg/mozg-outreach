from pathlib import Path

import pytest

from mozg_outreach.agents import PromptLibrary
from mozg_outreach.cvs.models import CvOption, CvPickInput, CvRating, CvRatings, CvSummary
from mozg_outreach.cvs.picker import CvPicker, rank
from mozg_outreach.pipelines.seeker.models import Listing
from mozg_outreach.pipelines.steps import PipelineSteps
from tests.pipelines.conftest import REPO_PROMPTS, RecordedRunner


def option(cv_id: int, language: str = "en", headline: str = "Backend engineer") -> CvOption:
    summary = CvSummary(
        language=language,
        headline=headline,
        seniority="senior",
        titles=[headline],
        domains=["payments"],
        core_skills=["Python"],
    )
    return CvOption(id=cv_id, name=f"CV {cv_id}", summary=summary, excerpt="text")


def rating(cv_id: int, skills: int, title: int = 2, seniority: int = 2) -> CvRating:
    return CvRating(
        cv_id=cv_id, skills=skills, title=title, seniority=seniority, domain=2, reason=f"r{cv_id}"
    )


LISTING = Listing(
    key="https://jobs.example/1",
    kind="job",
    title="Senior Python Engineer",
    company="Acme",
    location_string="Remote",
    remote_status_raw="remote",
    url="https://jobs.example/1",
    sources=["remoteok"],
    posted_on=None,
    pay=None,
    summary="We need a senior Python engineer for our payments platform.",
)


class TestRank:
    def test_best_total_wins_and_python_computes_it(self) -> None:
        ratings = CvRatings(
            listing_language="en", ratings=[rating(1, skills=2), rating(2, skills=4)], concerns=[]
        )
        choice = rank([option(1), option(2)], ratings)
        assert choice.cv_id == 2
        assert choice.picked_by == "ratings"
        # skills 40, title 25, seniority 20, domain 15, each scaled by rating / 4
        assert [(r.cv_id, r.total) for r in choice.ranked] == [(2, 70.0), (1, 50.0)]

    def test_a_cv_in_the_listing_language_beats_a_better_rated_one(self) -> None:
        ratings = CvRatings(
            listing_language="de", ratings=[rating(1, skills=4), rating(2, skills=1)], concerns=[]
        )
        choice = rank([option(1, "en"), option(2, "de")], ratings)
        assert choice.cv_id == 2
        assert [r.in_listing_language for r in choice.ranked] == [True, False]

    def test_without_a_cv_in_that_language_all_compete(self) -> None:
        ratings = CvRatings(
            listing_language="fr", ratings=[rating(1, skills=1), rating(2, skills=3)], concerns=[]
        )
        choice = rank([option(1), option(2)], ratings)
        assert choice.cv_id == 2
        assert any("fr" in c for c in choice.concerns)

    def test_ties_keep_library_order_and_unrated_cvs_rank_last(self) -> None:
        ratings = CvRatings(
            listing_language="en", ratings=[rating(3, skills=2), rating(1, skills=2)], concerns=[]
        )
        choice = rank([option(1), option(2), option(3)], ratings)
        assert [r.cv_id for r in choice.ranked] == [1, 3, 2]
        assert choice.ranked[-1].total is None
        assert any("CV 2" in c for c in choice.concerns)


class TestPicker:
    @pytest.fixture
    def runner(self) -> RecordedRunner:
        return RecordedRunner({})

    @pytest.fixture
    def picker(self, runner: RecordedRunner, tmp_path: Path) -> CvPicker:
        return CvPicker(PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path))

    async def test_one_cv_is_picked_without_an_agent(self, picker: CvPicker) -> None:
        choice = await picker.pick(LISTING, [option(7)])
        assert (choice.cv_id, choice.picked_by, choice.ranked) == (7, "only", [])

    async def test_agent_sees_every_cv_in_a_shuffled_order(
        self, picker: CvPicker, runner: RecordedRunner
    ) -> None:
        cvs = [option(n) for n in range(1, 7)]
        scripted = CvRatings(
            listing_language="en",
            ratings=[rating(n, skills=n % 5) for n in range(1, 7)],
            concerns=[],
        )
        runner._script["cv-picker"] = [scripted]
        choice = await picker.pick(LISTING, cvs)
        [sent] = runner.payloads["cv-picker"]
        assert isinstance(sent, CvPickInput)
        order = [c.id for c in sent.cvs]
        assert sorted(order) == [1, 2, 3, 4, 5, 6]
        assert order != [1, 2, 3, 4, 5, 6]
        assert choice.cv_id == 4
