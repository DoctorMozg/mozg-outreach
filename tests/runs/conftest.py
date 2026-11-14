from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mozg_outreach.agents import PromptLibrary
from mozg_outreach.domain import ProjectMode
from mozg_outreach.pipelines.job.models import JobBrief, JudgeResult
from mozg_outreach.pipelines.summary import RunSummary
from mozg_outreach.runs.catalog import FLOWS
from mozg_outreach.runs.manager import RunEnvironment, RunManager
from mozg_outreach.sources.base import SourceAdapter
from mozg_outreach.sources.models import FetchResult, SourceItem, SourceQuery
from mozg_outreach.storage.db import Database
from mozg_outreach.storage.models import JsonObject, Project, SourceConfig
from tests.pipelines.conftest import REPO_PROMPTS, RecordedRunner, Scripted
from tests.pipelines.job.conftest import CV_TEXT, judgment
from tests.pipelines.job.conftest import strategy as strategy  # fixture re-export

NOW = datetime(2026, 11, 13, 9, tzinfo=UTC)
SUMMARIZER = "seeker-run-summarizer"
QUIET_SUMMARY = RunSummary(summary="Quiet run.", patterns=[], recommendations=[], concerns=[])
FEED_URL = "https://boards.greenhouse.io/acme/jobs/1"


class FakeFeed:
    name = "greenhouse"

    def __init__(self) -> None:
        self.cursors: list[str | None] = []
        self.extra: list[SourceItem] = []

    async def fetch(self, query: SourceQuery, cursor: str | None) -> FetchResult:
        self.cursors.append(cursor)
        item = SourceItem(
            source="greenhouse",
            source_item_id="acme/1",
            url=FEED_URL,
            kind="job",
            title="Senior Backend Engineer",
            raw_text="Python and PostgreSQL services on Kubernetes. Remote within the EU.",
            raw_payload={},
            published_at=NOW,
            fetched_at=NOW,
            org_name="Acme",
            location="Remote - EU",
        )
        return FetchResult(items=[item, *self.extra], cursor="2026-11-13T09:00:00+00:00")


class Harness:
    """A run manager over a temp database, with scripted agents and one fake feed."""

    def __init__(self, db: Database, tmp_path: Path, runner: RecordedRunner | None = None) -> None:
        self.db = db
        self.feed = FakeFeed()
        # Kinds that need a real adapter; every other kind gets the fake feed.
        self.adapters: dict[str, Callable[[JsonObject], SourceAdapter]] = {}
        self.runner = runner or RecordedRunner({})
        self.manager = RunManager(
            RunEnvironment(
                db=db,
                runner=self.runner,
                prompts=PromptLibrary(REPO_PROMPTS),
                workspaces=tmp_path / "workspaces",
                adapters=lambda kind, config: (
                    self.adapters[kind](config) if kind in self.adapters else self.feed
                ),
            ),
            FLOWS,
        )

    def script(self, script: dict[str, list[Scripted]]) -> None:
        """Each scripted search also gets a run summary unless the script brings its own."""
        if "job-strategist" in script or "freelance-strategist" in script:
            quiet: dict[str, list[Scripted]] = {SUMMARIZER: [QUIET_SUMMARY]}
            script = quiet | script
        for name, results in script.items():
            self.runner._script.setdefault(name, []).extend(results)

    def job_project(self) -> int:
        with self.db.session() as session:
            brief: JsonObject = JobBrief(cv_text=CV_TEXT, preferences="Remote EU").model_dump()
            project = Project(
                name="Jobs", mode=ProjectMode.JOB, sender_name="Stan Petrov", brief=brief
            )
            session.add(project)
            session.flush()
            session.add(
                SourceConfig(project_id=project.id, kind="greenhouse", config={"boards": ["acme"]})
            )
            return project.id


@pytest.fixture
def harness(db: Database, tmp_path: Path) -> Harness:
    return Harness(db, tmp_path)


def judged(key: str = FEED_URL) -> JudgeResult:
    return JudgeResult(judgments=[judgment(key)], concerns=[])
