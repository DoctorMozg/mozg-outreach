from datetime import UTC, datetime

import pytest

from mozg_outreach.agents import AgentRunError
from mozg_outreach.api.schemas import ItemDetail, ItemOut
from mozg_outreach.domain import ItemKind, RunStatus
from mozg_outreach.pipelines.biz.assessment_models import FitVerdict
from mozg_outreach.pipelines.biz.models import (
    BriefReview,
    ClimateReport,
    ClimateSources,
    ScoutResult,
    SourceList,
    Strategy,
)
from mozg_outreach.pipelines.fields import FieldProposal, FieldResearchResult
from mozg_outreach.pipelines.summary import Pattern, RunSummary
from mozg_outreach.storage.models import Item, JsonObject, Run
from tests.pipelines.biz.conftest import load_fixture
from tests.pipelines.conftest import Scripted

from .conftest import Api, biz_brief, sse_events


@pytest.fixture
def strategy(strategy: Strategy) -> Strategy:
    """One target sector, so each round runs one source researcher and one scout."""
    strategy.target_profile.sectors = strategy.target_profile.sectors[:1]
    return strategy


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


SUMMARY = RunSummary(
    summary="Two leads, both thinly assessed.",
    patterns=[Pattern(pattern="Both sell to manufacturers.", item_ids=[1, 2])],
    recommendations=["Research headcount before writing."],
    concerns=[],
)


# A search the scripts cover: no market climate step and one directory per sector.
SMALL: dict[str, object] = {"limit": 20, "climate": False, "sources_per_sector": 1}


def search_script(strategy: Strategy) -> dict[str, list[Scripted]]:
    sources = load_fixture("sources", SourceList)
    two_sources = sources.model_copy(update={"sources": sources.sources[:2]})
    return {
        "outreach-strategist": [strategy, strategy],
        "outreach-source-researcher": [two_sources],
        # The second scout is the search round for a sector still short of its quota.
        "outreach-scout": [
            load_fixture("scout_results", list[ScoutResult])[0],
            ScoutResult(companies=[], concerns=[]),
        ],
        "field-researcher": [load_fixture("field_research", FieldResearchResult)] * 2,
        "fit-judge": [load_fixture("fit_verdict", FitVerdict)] * 2,
        "run-summarizer": [SUMMARY],
    }


async def test_the_default_search_researches_the_market_first(
    api: Api, strategy: Strategy, proposal: FieldProposal
) -> None:
    project_id = await api.project(brief=biz_brief())
    await accepted_spec(api, project_id, proposal)
    report = load_fixture("climate_report", ClimateReport)
    api.script(
        {
            "outreach-climate-source-finder": [load_fixture("climate_sources", ClimateSources)],
            "outreach-climate-researcher": [report],
            "outreach-strategist": [strategy],
        }
    )

    run = await api.ok("POST", f"/api/projects/{project_id}/runs", {})
    parked = await api.settle(int(str(run["id"])))

    assert parked["status"] == "awaiting_gate", parked["error"]
    state = parked["state"]
    assert isinstance(state, dict)
    options = state["options"]
    assert isinstance(options, dict)
    assert (options["limit"], options["sources_per_sector"]) == (50, 3)
    climate = state["climate"]
    assert isinstance(climate, dict)
    assert climate["climate_summary"] == report.climate_summary
    planned = api.runner.payloads["outreach-strategist"][0].model_dump()
    assert planned["climate"]["climate_summary"] == report.climate_summary


async def test_brief_to_scored_report_through_the_strategy_gate(
    api: Api, strategy: Strategy, proposal: FieldProposal
) -> None:
    project_id = await api.project(brief=biz_brief())
    await accepted_spec(api, project_id, proposal)
    api.script(search_script(strategy))

    run = await api.ok(
        "POST",
        f"/api/projects/{project_id}/runs",
        {"options": {**SMALL, "limit": 5}},
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
    scouts = [p.model_dump() for p in api.runner.payloads["outreach-scout"]]
    assert scouts[0]["source"]["name"] == load_fixture("sources", SourceList).sources[0].name
    assert scouts[1]["source"] is None, "a short sector gets a web-search round"
    assert {c.title for c in report} <= set(scouts[1]["exclude_names"])
    researched = {f.id for f in proposal.spec.fields} & set(report[0].fields)
    assert researched, "researched field values reach the report"
    for item in report:
        detail = await api.get(f"/api/items/{item.id}", ItemDetail)
        assert [(e.kind, e.role) for e in detail.entities] == [("company", "subject")]
    state = done["state"]
    assert isinstance(state, dict)
    assert state["summary"] == SUMMARY.model_dump(mode="json")
    sent = api.runner.payloads["run-summarizer"][0].model_dump()
    assert {i["id"] for i in sent["items"]} == {item.id for item in report}


async def test_an_archived_company_found_again_is_not_assessed_again(
    api: Api, strategy: Strategy, proposal: FieldProposal
) -> None:
    project_id = await api.project(brief=biz_brief())
    await accepted_spec(api, project_id, proposal)
    with api.services.db.session() as session:
        session.add(
            Item(
                project_id=project_id,
                kind=ItemKind.COMPANY,
                dedup_key="katana-earlier",
                title="Katana",
                archived_at=datetime(2026, 11, 16, tzinfo=UTC),
            )
        )
    api.script(search_script(strategy))

    run = await api.ok(
        "POST",
        f"/api/projects/{project_id}/runs",
        {"options": {**SMALL, "limit": 5}},
    )
    run_id = int(str(run["id"]))
    await api.settle(run_id)
    await api.ok("POST", f"/api/runs/{run_id}/gate", {"approve": True})
    done = await api.settle(run_id)

    assert done["status"] == "complete", done["error"]
    report = await api.get(f"/api/projects/{project_id}/items", list[ItemOut])
    assert [i.title for i in report] == ["Blackwall (formerly BotGuard)"]
    assert len(api.runner.payloads["fit-judge"]) == 1
    archived = await api.get(f"/api/projects/{project_id}/items?archived=true", list[ItemOut])
    assert [(i.title, i.score_total) for i in archived] == [("Katana", None)]


class TestNeedsAcceptedFields:
    """Assessment reads the accepted fields, so a biz search neither starts nor resumes without."""

    async def test_start_is_refused_before_any_agent_runs(self, api: Api) -> None:
        project_id = await api.project(brief=biz_brief())
        refused = await api.client.post(f"/api/projects/{project_id}/runs", json={})
        assert refused.status_code == 409
        assert "Fields tab" in refused.json()["detail"]
        assert await api.ok_list(f"/api/projects/{project_id}/runs") == []

    async def test_resume_is_refused_and_the_run_stays_failed(self, api: Api) -> None:
        project_id = await api.project(brief=biz_brief())
        with api.services.db.session() as session:
            run = Run(
                project_id=project_id,
                pipeline="biz.search",
                phase="assess",
                status=RunStatus.FAILED,
                error="ValueError: accept a field spec before running the search",
            )
            session.add(run)
            session.flush()
            run_id = run.id
        refused = await api.client.post(f"/api/runs/{run_id}/resume")
        assert refused.status_code == 409
        assert "Fields tab" in refused.json()["detail"]
        assert (await api.ok("GET", f"/api/runs/{run_id}"))["status"] == "failed"


async def approved_run(api: Api, strategy: Strategy, options: dict[str, object]) -> JsonObject:
    project_id = await api.project(brief=biz_brief())
    api.script(
        {
            "brief-reviewer": [load_fixture("brief_review", BriefReview)],
            "field-proposer": [load_fixture("field_proposal", FieldProposal)],
        }
    )
    fields_run = await api.ok("POST", f"/api/projects/{project_id}/fields/propose")
    await api.settle(int(str(fields_run["id"])))
    await api.ok("POST", f"/api/projects/{project_id}/fields/1/accept")
    run = await api.ok("POST", f"/api/projects/{project_id}/runs", {"options": SMALL | options})
    run_id = int(str(run["id"]))
    await api.settle(run_id)
    await api.ok("POST", f"/api/runs/{run_id}/gate", {"approve": True})
    return await api.settle(run_id)


class TestRunSummary:
    async def test_switched_off_skips_the_summarizer(self, api: Api, strategy: Strategy) -> None:
        script = search_script(strategy)
        del script["run-summarizer"]
        api.script(script)

        done = await approved_run(api, strategy, {"summary": False})

        assert done["status"] == "complete", done["error"]
        assert "run-summarizer" not in api.runner.payloads

    async def test_a_failed_summary_leaves_the_run_complete(
        self, api: Api, strategy: Strategy
    ) -> None:
        script = search_script(strategy)
        script["run-summarizer"] = [
            AgentRunError("run-summarizer", "error_max_turns", "out of turns")
        ]
        api.script(script)

        done = await approved_run(api, strategy, {})

        assert done["status"] == "complete", done["error"]
        state = done["state"]
        assert isinstance(state, dict)
        assert state["summary"] is None
        assert "No run summary: run-summarizer: run failed (error_max_turns): out of turns" in str(
            state["concerns"]
        )


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
