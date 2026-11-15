from mozg_outreach.agents import AgentRunError
from mozg_outreach.domain import RunStatus
from mozg_outreach.pipelines.seeker.models import JobStrategy
from mozg_outreach.pipelines.summary import Pattern, RunSummary
from mozg_outreach.runs.flow import GateAnswer
from mozg_outreach.runs.job import JobSearchState
from mozg_outreach.storage.models import JsonObject, Run
from tests.pipelines.conftest import Scripted
from tests.pipelines.leads.conftest import identity

from .conftest import SUMMARIZER, Harness, judged


async def job_run(
    harness: Harness, strategy: JobStrategy, summary: Scripted, options: JsonObject
) -> JobSearchState:
    project_id = harness.job_project()
    harness.script(
        {
            "job-strategist": [strategy],
            "seeker-judge": [judged()],
            "lead-identifier": [identity()],
            SUMMARIZER: [summary],
        }
    )
    run_id = harness.manager.start(project_id, "job.search", {"options": options})
    await harness.manager.wait(run_id)
    harness.manager.answer_gate(run_id, GateAnswer(approve=True))
    await harness.manager.wait(run_id)
    with harness.db.session() as session:
        run = session.get(Run, run_id)
        assert run is not None
        session.expunge(run)
    assert run.status == RunStatus.COMPLETE, run.error
    return JobSearchState.model_validate(run.state)


def cites(*item_ids: int) -> RunSummary:
    pattern = Pattern(pattern="Both are Python platform teams.", item_ids=list(item_ids))
    return RunSummary(
        summary="One strong lead.", patterns=[pattern], recommendations=[], concerns=[]
    )


class TestSeekerSummary:
    async def test_summary_reads_the_scored_leads_and_drops_unknown_citations(
        self, harness: Harness, strategy: JobStrategy
    ) -> None:
        state = await job_run(harness, strategy, cites(1, 99), {})

        [sent] = harness.runner.payloads[SUMMARIZER]
        payload = sent.model_dump()
        assert (payload["mode"], payload["preferences"]) == ("job", "Remote EU")
        assert payload["must_have_skills"] == strategy.must_have_skills
        assert [i["id"] for i in payload["items"]] == state.item_ids
        assert state.summary is not None
        assert state.summary.patterns[0].item_ids == [1]
        assert any("99" in c for c in state.concerns)

    async def test_a_failed_summary_leaves_the_run_complete(
        self, harness: Harness, strategy: JobStrategy
    ) -> None:
        failure = AgentRunError(SUMMARIZER, "error_during_execution", "rate limited")

        state = await job_run(harness, strategy, failure, {})

        assert state.summary is None
        assert any(c.startswith("No run summary") for c in state.concerns)

    async def test_switched_off_skips_the_summarizer(
        self, harness: Harness, strategy: JobStrategy
    ) -> None:
        state = await job_run(harness, strategy, cites(1), {"summary": False})

        assert SUMMARIZER not in harness.runner.payloads
        assert state.summary is None
