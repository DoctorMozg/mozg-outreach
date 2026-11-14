from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mozg_outreach.domain import RunStatus
from mozg_outreach.pipelines.job.models import JobStrategy, JudgeResult
from mozg_outreach.runs.flow import GateAnswer
from mozg_outreach.runs.job import ChannelChoice, _discovery_terms
from mozg_outreach.sources import telegram_discovery
from mozg_outreach.sources.telegram import (
    ChannelMessage,
    TelegramAdapter,
    TelegramConfig,
    TelegramCredentials,
)
from mozg_outreach.storage.models import JsonObject, Run, SourceConfig
from tests.pipelines.conftest import Scripted
from tests.pipelines.job.conftest import judgment
from tests.pipelines.leads.conftest import identity
from tests.sources.conftest import SearchSession, channel

from .conftest import FEED_URL, Harness

# Designer posts carry job markers but not the strategy's must-have skills, so screening drops
# them and the run's single scripted judgment still covers every listing that survives.
DESIGN_JOB = "Hiring a product designer, remote, salary 4k"


def recent(*texts: str) -> list[ChannelMessage]:
    date = datetime.now(UTC) - timedelta(days=1)
    return [ChannelMessage(id=i + 1, text=t, date=date) for i, t in enumerate(texts)]


@pytest.fixture
def session(harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SearchSession:
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("MOZG_TELEGRAM_SESSION", str(tmp_path / "tg" / "telegram"))
    monkeypatch.setattr(telegram_discovery, "PAUSE_SECONDS", 0)
    fake = SearchSession(
        {"Python jobs": [channel("py_jobs", 5000), channel("py_more", 800)]},
        {"py_jobs": recent(DESIGN_JOB, DESIGN_JOB), "py_more": recent(DESIGN_JOB)},
    )

    def build(config: JsonObject) -> TelegramAdapter:
        def factory(_credentials: TelegramCredentials, _path: Path) -> SearchSession:
            return fake

        return TelegramAdapter(TelegramConfig.model_validate(config), session_factory=factory)

    harness.adapters["telegram"] = build
    return fake


def telegram_project(harness: Harness) -> int:
    project_id = harness.job_project()
    with harness.db.session() as session:
        config = {"discover": True, "kind": "job"}
        session.add(SourceConfig(project_id=project_id, kind="telegram", config=config))
    return project_id


def telegram_config(harness: Harness) -> TelegramConfig:
    with harness.db.session() as session:
        row = session.query(SourceConfig).filter_by(kind="telegram").one()
        return TelegramConfig.model_validate(row.config)


def script(harness: Harness, strategy: JobStrategy) -> None:
    runs: dict[str, list[Scripted]] = {
        "job-strategist": [strategy],
        "job-listing-judge": [JudgeResult(judgments=[judgment(FEED_URL)], concerns=[])],
        "lead-identifier": [identity()],
    }
    harness.script(runs)


async def answer(harness: Harness, run_id: int, gate: GateAnswer) -> Run:
    harness.manager.answer_gate(run_id, gate)
    await harness.manager.wait(run_id)
    with harness.db.session() as session:
        run = session.get(Run, run_id)
        assert run is not None
        session.expunge(run)
        return run


class TestChannelDiscovery:
    async def test_approved_channels_are_read_and_rejected_ones_never_offered_again(
        self, harness: Harness, strategy: JobStrategy, session: SearchSession
    ) -> None:
        project_id = telegram_project(harness)
        script(harness, strategy)
        run_id = harness.manager.start(project_id, "job.search", {})
        await harness.manager.wait(run_id)
        parked = await answer(harness, run_id, GateAnswer(approve=True))

        assert parked.pending_gate is not None
        assert parked.pending_gate["kind"] == "channels"
        payload = parked.pending_gate["payload"]
        assert isinstance(payload, dict)
        offered = ChannelChoice.model_validate(payload["found"])
        assert [c.username for c in offered.channels] == ["py_jobs", "py_more"]

        keep = ChannelChoice(channels=offered.channels[:1]).model_dump(mode="json")
        done = await answer(harness, run_id, GateAnswer(approve=True, revised=keep))
        assert done.status == RunStatus.COMPLETE, done.error
        saved = telegram_config(harness)
        assert (saved.channels, saved.excluded) == (["py_jobs"], ["py_more"])
        # Vetting read both; collect then read only the approved one.
        assert session.reads == [("py_jobs", 0), ("py_more", 0), ("py_jobs", 0)]

        script(harness, strategy)
        again = harness.manager.start(project_id, "job.search", {})
        await harness.manager.wait(again)
        rerun = await answer(harness, again, GateAnswer(approve=True))
        assert rerun.status == RunStatus.COMPLETE, "nothing new to offer, so no channel gate"
        assert telegram_config(harness).channels == ["py_jobs"]
        assert session.reads[-1] == ("py_jobs", 2), "the second run resumes from the cursor"

    async def test_full_channel_list_skips_the_search(
        self, harness: Harness, strategy: JobStrategy, session: SearchSession
    ) -> None:
        project_id = harness.job_project()
        with harness.db.session() as db:
            config = {"discover": True, "channels": ["a_jobs"], "max_channels": 1}
            db.add(SourceConfig(project_id=project_id, kind="telegram", config=config))
        script(harness, strategy)
        run_id = harness.manager.start(project_id, "job.search", {})
        await harness.manager.wait(run_id)

        done = await answer(harness, run_id, GateAnswer(approve=True))
        assert done.status == RunStatus.COMPLETE
        assert session.queries == []


def test_discovery_searches_cv_skills_when_the_gate_cleared_must_haves(
    strategy: JobStrategy,
) -> None:
    cleared = strategy.model_copy(update={"must_have_skills": [], "must_have_or_groups": []})

    terms = _discovery_terms(cleared)

    assert terms[:4] == ["Python", "Go", "PostgreSQL", "Kubernetes"]
    assert len(terms) <= telegram_discovery.MAX_QUERIES
