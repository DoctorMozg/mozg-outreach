import pytest

from mozg_outreach.api.schemas import ItemOut
from mozg_outreach.pipelines.biz.assessment_models import FieldResearchResult, FitVerdict
from mozg_outreach.pipelines.biz.models import (
    BriefReview,
    FieldProposal,
    ScoutResult,
    SourceList,
    Strategy,
)
from tests.pipelines.biz.conftest import load_fixture
from tests.pipelines.conftest import Scripted

from .conftest import Api, biz_brief, sse_events


@pytest.fixture
def proposal() -> FieldProposal:
    return load_fixture("field_proposal", FieldProposal)


async def accepted_spec(api: Api, project_id: int, proposal: FieldProposal) -> None:
    api.script(
        {
            "brief-reviewer": [load_fixture("brief_review", BriefReview)],
            "field-proposer": [proposal],
        }
    )
    run = await api.ok("POST", f"/api/projects/{project_id}/fields/propose")
    done = await api.settle(int(str(run["id"])))
    assert done["status"] == "complete"
    [draft] = await api.ok_list(f"/api/projects/{project_id}/fields")
    assert draft["accepted_at"] is None
    await api.ok("POST", f"/api/projects/{project_id}/fields/1/accept")


def search_script(strategy: Strategy) -> dict[str, list[Scripted]]:
    sources = load_fixture("sources", SourceList)
    two_sources = sources.model_copy(update={"sources": sources.sources[:2]})
    return {
        "outreach-strategist": [strategy, strategy],
        "outreach-source-researcher": [two_sources],
        "outreach-scout": [load_fixture("scout_results", list[ScoutResult])[0]],
        "field-researcher": [load_fixture("field_research", FieldResearchResult)] * 2,
        "fit-judge": [load_fixture("fit_verdict", FitVerdict)] * 2,
    }


async def test_brief_to_scored_report_through_the_strategy_gate(
    api: Api, strategy: Strategy, proposal: FieldProposal
) -> None:
    project_id = await api.project(brief=biz_brief())
    await accepted_spec(api, project_id, proposal)
    api.script(search_script(strategy))

    run = await api.ok(
        "POST", f"/api/projects/{project_id}/runs", {"options": {"limit": 5, "max_sources": 1}}
    )
    run_id = int(str(run["id"]))
    parked = await api.settle(run_id)
    assert parked["status"] == "awaiting_gate"
    gate = parked["pending_gate"]
    assert isinstance(gate, dict)
    assert gate["kind"] == "strategy"

    busy = await api.client.post(f"/api/projects/{project_id}/runs", json={})
    assert busy.status_code == 409

    await api.ok("POST", f"/api/runs/{run_id}/gate", {"approve": False, "feedback": "Only Berlin"})
    await api.settle(run_id)
    replan = api.runner.payloads["outreach-strategist"][1].model_dump()
    assert replan["feedback"] == "Only Berlin"
    await api.ok("POST", f"/api/runs/{run_id}/gate", {"approve": True})
    done = await api.settle(run_id)
    assert done["status"] == "complete", done["error"]

    report = await api.get(f"/api/projects/{project_id}/items", list[ItemOut])
    assert len(report) == 2
    assert all(item.score_total is not None for item in report)
    assert len(api.runner.payloads["outreach-scout"]) == 1, "max_sources caps the scouts"
    researched = {f.id for f in proposal.spec.fields} & set(report[0].fields)
    assert researched, "researched field values reach the report"


async def test_events_replay_from_last_event_id(api: Api, proposal: FieldProposal) -> None:
    project_id = await api.project(brief=biz_brief())
    await accepted_spec(api, project_id, proposal)
    events = await api.client.get("/api/runs/1/events")
    replayed = sse_events(events.text)
    kinds = [e["kind"] for e in replayed]
    assert kinds[0] == "status"
    assert "brief_review" in kinds
    assert kinds[-1] == "status"

    tail = await api.client.get("/api/runs/1/events", headers={"Last-Event-ID": "3"})
    assert [e["seq"] for e in sse_events(tail.text)] == list(range(4, len(replayed) + 1))
