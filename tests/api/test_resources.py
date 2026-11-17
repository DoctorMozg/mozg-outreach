from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from mozg_outreach.api.routes import registry as registry_routes
from mozg_outreach.api.schemas import EntityDetail, EntityOut, GraphOut, ItemOut
from mozg_outreach.domain import EntityKind, ItemKind, SourceKind
from mozg_outreach.naturalize.voice import VoiceNotes
from mozg_outreach.pipelines.biz.models import Strategy
from mozg_outreach.sources.factory import DEFAULT_SOURCES
from mozg_outreach.storage.models import (
    Draft,
    Entity,
    Item,
    ItemLink,
    JsonObject,
    MergeSuggestion,
    Relation,
    Run,
    Sighting,
)
from tests.pipelines.conftest import HangingRunner
from tests.pipelines.seeker.conftest import CV_TEXT

from .conftest import Api, biz_brief, open_api


class TestRuns:
    async def test_cancel_through_the_api(self, tmp_path: Path) -> None:
        runner = HangingRunner()
        async for api in open_api(tmp_path, runner):
            project_id = await api.biz_project()
            run = await api.ok("POST", f"/api/projects/{project_id}/runs", {})
            await runner.started.wait()
            cancelled = await api.ok("POST", f"/api/runs/{run['id']}/cancel")
            assert cancelled["status"] == "cancelled"
            again = await api.client.post(f"/api/runs/{run['id']}/cancel")
            assert again.status_code == 409

    async def test_gate_errors(self, api: Api, strategy: Strategy) -> None:
        project_id = await api.biz_project()
        api.script({"outreach-strategist": [strategy]})
        run = await api.ok("POST", f"/api/projects/{project_id}/runs", {})
        await api.settle(int(str(run["id"])))
        bad_edit = await api.client.post(
            f"/api/runs/{run['id']}/gate", json={"approve": True, "revised": {"goal": 1}}
        )
        assert bad_edit.status_code == 422
        assert (await api.client.get("/api/runs/999")).status_code == 404

    async def test_pipelines_list_their_phases_in_order(self, api: Api) -> None:
        pipelines = await api.ok("GET", "/api/pipelines")
        assert pipelines["biz.search"] == [
            "plan",
            "approve_strategy",
            "sources",
            "scout",
            "assess",
            "summarize",
        ]
        assert pipelines["biz.letters"] == ["letters"]
        assert pipelines["biz.approach"] == ["approach", "letters"]
        assert pipelines["seeker.apply"] == ["fill", "review"]

    async def test_search_needs_a_brief(self, api: Api) -> None:
        project_id = await api.project()
        assert (
            await api.client.post(f"/api/projects/{project_id}/runs", json={})
        ).status_code == 409


class TestSearchReadiness:
    """The setup checklist shows what blocks a search, from the same check that refuses it."""

    async def blocker(self, api: Api, project_id: int) -> object:
        return (await api.ok("GET", f"/api/projects/{project_id}/search"))["blocker"]

    async def test_biz_needs_a_brief_then_accepted_fields(self, api: Api) -> None:
        bare = await api.project()
        assert "brief" in str(await self.blocker(api, bare))
        briefed = await api.project(brief=biz_brief())
        assert "Fields tab" in str(await self.blocker(api, briefed))
        ready = await api.biz_project()
        assert await api.ok("GET", f"/api/projects/{ready}/search") == {
            "pipeline": "biz.search",
            "blocker": None,
        }

    async def test_seeker_needs_only_a_brief(self, api: Api) -> None:
        bare = await api.project("seeker")
        assert "brief" in str(await self.blocker(api, bare))
        ready = await api.project("seeker", {"cv_text": CV_TEXT, "preferences": "Remote EU"})
        assert await self.blocker(api, ready) is None


class TestProjects:
    async def test_brief_is_validated_against_the_mode(self, api: Api) -> None:
        bad = await api.client.post(
            "/api/projects", json={"name": "x", "mode": "seeker", "brief": biz_brief()}
        )
        assert bad.status_code == 422
        project_id = await api.project("seeker", {"cv_text": CV_TEXT, "preferences": "Remote EU"})
        patched = await api.ok("PATCH", f"/api/projects/{project_id}", {"sender_tone": "warm"})
        assert patched["sender_tone"] == "warm"
        assert patched["brief"] is not None

    async def test_schemas_describe_brief_and_options(self, api: Api) -> None:
        schemas = await api.ok("GET", "/api/modes/biz/schemas")
        assert set(schemas) == {"brief", "options"}
        assert "offering" in str(schemas["brief"])
        assert "limit" in str(schemas["options"])

    async def test_feeds_belong_to_seeker_projects(self, api: Api) -> None:
        biz = await api.project(brief=biz_brief())
        refused = await api.client.post(
            f"/api/projects/{biz}/sources", json={"kind": "greenhouse", "config": {}}
        )
        assert refused.status_code == 422
        seeker = await api.project("seeker")
        added = await api.ok(
            "POST",
            f"/api/projects/{seeker}/sources",
            {"kind": "greenhouse", "config": {"boards": ["acme"]}},
        )
        kinds = [s["kind"] for s in await api.ok_list(f"/api/projects/{seeker}/sources")]
        assert kinds[-1] == "greenhouse"
        assert added["cursor"] is None

    async def test_new_projects_enable_every_source_that_needs_no_setup(self, api: Api) -> None:
        biz = await api.ok_list(f"/api/projects/{await api.project(brief=biz_brief())}/sources")
        assert [s["kind"] for s in biz] == ["web_search"]
        seeker = await api.ok_list(f"/api/projects/{await api.project('seeker')}/sources")
        by_kind = {s["kind"]: s for s in seeker}
        # ATS boards need company names from the user, so only they stay opt-in.
        assert set(by_kind) == {
            "web_search",
            "arbeitnow",
            "hn_whoishiring",
            "himalayas",
            "remoteok",
            "remotive",
            "weworkremotely",
            "workingnomads",
            "telegram",
        }
        assert all(s["enabled"] for s in seeker)
        assert by_kind["telegram"]["config"] == {"discover": True}
        for kind, config in DEFAULT_SOURCES.items():
            api.services.adapters(kind, config)

    async def test_only_the_same_item_waits_for_its_open_run(self, api: Api) -> None:
        project_id = await api.project("seeker")
        with api.services.db.session() as session:
            rows = [
                Item(project_id=project_id, kind=ItemKind.JOB, dedup_key=k, title=k)
                for k in ("a", "b")
            ]
            session.add_all(rows)
            session.flush()
            busy, free = rows[0].id, rows[1].id
            state: JsonObject = {"item_id": busy}
            session.add(Run(project_id=project_id, pipeline="seeker.enrich", state=state))
        clash = await api.client.post(f"/api/items/{busy}/enrich")
        assert clash.status_code == 409
        assert (await api.client.post(f"/api/items/{free}/enrich")).status_code == 202

    async def test_delete_removes_the_project_but_keeps_the_registry(self, api: Api) -> None:
        project_id = await api.project("seeker")
        with api.services.db.session() as session:
            run = Run(project_id=project_id, pipeline="seeker.search", state={})
            item = Item(project_id=project_id, kind=ItemKind.JOB, dedup_key="j", title="Job")
            acme = Entity(kind=EntityKind.COMPANY, name="Acme")
            session.add_all([run, item, acme])
            session.flush()
            seen = Sighting(
                entity_id=acme.id,
                source_kind=SourceKind.FEED,
                source_url="https://acme.example",
                project_id=project_id,
                run_id=run.id,
            )
            session.add(seen)
            session.flush()
            run_id, sighting_id = run.id, seen.id

        open_run = await api.client.delete(f"/api/projects/{project_id}")
        assert open_run.status_code == 409
        with api.services.db.session() as session:
            finished = session.get(Run, run_id)
            assert finished is not None
            finished.finished_at = datetime.now(UTC)
        assert (await api.client.delete(f"/api/projects/{project_id}")).status_code == 204

        assert (await api.client.get(f"/api/projects/{project_id}")).status_code == 404
        with api.services.db.session() as session:
            assert session.query(Item).count() == 0
            assert session.query(Run).count() == 0
            sighting = session.get(Sighting, sighting_id)
            assert sighting is not None
            assert (sighting.project_id, sighting.run_id) == (None, None)

    async def test_item_actions_need_a_pipeline(self, api: Api) -> None:
        project_id = await api.project("seeker")
        with api.services.db.session() as session:
            item = Item(project_id=project_id, kind=ItemKind.GIG, dedup_key="g", title="Gig")
            session.add(item)
            session.flush()
            item_id = item.id
        assert (await api.client.post(f"/api/items/{item_id}/enrich")).status_code == 409
        assert (await api.client.post(f"/api/items/{item_id}/approach")).status_code == 409
        assert (await api.client.post(f"/api/items/{item_id}/apply")).status_code == 409
        detail = await api.ok("GET", f"/api/items/{item_id}")
        assert detail["drafts"] == []

    async def test_item_reports_when_it_was_last_enriched(self, api: Api) -> None:
        project_id = await api.biz_project()
        with api.services.db.session() as session:
            fresh = Item(project_id=project_id, kind=ItemKind.COMPANY, dedup_key="a", title="A")
            done = Item(
                project_id=project_id,
                kind=ItemKind.COMPANY,
                dedup_key="b",
                title="B",
                details={"dossier": {}, "enriched_at": "2026-11-10T09:30:00+00:00"},
            )
            session.add_all([fresh, done])
            session.flush()
            fresh_id, done_id = fresh.id, done.id
        assert (await api.ok("GET", f"/api/items/{fresh_id}"))["enriched_at"] is None
        enriched = await api.ok("GET", f"/api/items/{done_id}")
        assert enriched["enriched"] is True
        assert str(enriched["enriched_at"]).startswith("2026-11-10T09:30:00")
        assert (await api.ok("GET", f"/api/items/{fresh_id}"))["enriched"] is False


async def test_voice_profile_needs_samples_then_builds(api: Api) -> None:
    sample = "Hi Anna, thanks for the quick reply. Tuesday works, I will send the agenda tonight."
    for _ in range(2):
        await api.ok("POST", "/api/voice/email/samples", {"text": sample})
    early = await api.client.post("/api/voice/email/profile", json={})
    assert early.status_code == 409
    await api.ok("POST", "/api/voice/email/samples", {"text": sample})
    notes = VoiceNotes(tone="plain", habits=["short"], vocabulary=[], avoid=[], concerns=[])
    api.script({"voice-profiler": [notes]})
    built = await api.ok("POST", "/api/voice/email/profile", {"style_prompts": ["No jargon."]})
    assert "No jargon." in str(built["rendered"])
    stored = await api.ok("GET", "/api/voice/email/profile")
    assert stored["profile"] == built["profile"]


async def test_a_draft_revision_starts_the_items_letter_run(api: Api) -> None:
    project_id = await api.project("seeker")
    with api.services.db.session() as session:
        job = Item(project_id=project_id, kind=ItemKind.JOB, dedup_key="j", title="Job")
        session.add(job)
        session.flush()
        draft = Draft(item_id=job.id, channel="cover_letter", recipient="Hiring Team", version=1)
        draft.body, draft.review = "Dear Hiring Team,\n\nBody.\n\nThanks,\nStan", {}
        session.add(draft)
        session.flush()
        draft_id = draft.id
    ask = {"suggestion": "Write it in Russian."}
    run = await api.ok("POST", f"/api/drafts/{draft_id}/revise", ask)
    assert run["pipeline"] == "seeker.letters"
    with api.services.db.session() as session:
        stored = session.get(Run, run["id"])
        assert stored is not None
        assert stored.state["revision"] == {"draft_id": draft_id, "suggestion": ask["suggestion"]}
    await api.settle(int(str(run["id"])))
    missing = await api.client.post("/api/drafts/999/revise", json=ask)
    assert missing.status_code == 404


async def test_logged_activity_shows_in_every_list(api: Api) -> None:
    first, second = await api.project("seeker"), await api.project("seeker")
    with api.services.db.session() as session:
        job = Item(project_id=first, kind=ItemKind.JOB, dedup_key="https://a.example/1", title="A")
        twin = Item(project_id=second, kind=ItemKind.JOB, dedup_key=job.dedup_key, title="A")
        acme = Entity(kind=EntityKind.COMPANY, name="Acme")
        session.add_all([job, twin, acme])
        session.flush()
        session.add(ItemLink(item_id=twin.id, entity_id=acme.id, role="employer"))
        job_id, twin_id, acme_id = job.id, twin.id, acme.id
    await api.ok("POST", f"/api/items/{job_id}/interactions", {"kind": "sent", "note": "via form"})

    [row] = await api.get(f"/api/projects/{second}/items", list[ItemOut])
    assert row.activity is not None
    assert (row.activity.status, row.activity.note) == ("sent", "via form")
    assert row.activity.project_id == first
    detail = await api.get(f"/api/items/{twin_id}", ItemOut)
    assert detail.activity == row.activity
    [lead] = (await api.get(f"/api/entities/{acme_id}", EntityDetail)).leads
    assert lead.activity is not None
    assert lead.activity.status == "sent"

    # "refused" is the other side turning the user down, unlike "declined" by the user.
    await api.ok("POST", f"/api/items/{twin_id}/interactions", {"kind": "refused", "note": ""})
    refused = (await api.get(f"/api/items/{job_id}", ItemOut)).activity
    assert refused is not None
    assert (refused.status, refused.note) == ("refused", "via form")


class TestRegistry:
    def seed(self, session: Session, project_id: int) -> tuple[int, int, int, int]:
        acme = Entity(kind=EntityKind.COMPANY, name="Acme GmbH")
        twin = Entity(kind=EntityKind.COMPANY, name="Acme Gmbh.")
        jane = Entity(kind=EntityKind.PERSON, name="Jane Example")
        item = Item(project_id=project_id, kind=ItemKind.COMPANY, dedup_key="acme", title="Acme")
        session.add_all([acme, twin, jane, item])
        session.flush()
        session.add_all(
            [
                Relation(from_id=jane.id, to_id=acme.id, type="works_at"),
                ItemLink(item_id=item.id, entity_id=acme.id, role="target"),
                MergeSuggestion(entity_a_id=acme.id, entity_b_id=twin.id, reason="similar names"),
            ]
        )
        return acme.id, jane.id, twin.id, item.id

    async def test_profile_graph_and_merge(self, api: Api) -> None:
        project_id = await api.project(brief=biz_brief())
        with api.services.db.session() as session:
            acme, jane, twin, item = self.seed(session, project_id)

        profile = await api.get(f"/api/entities/{acme}", EntityDetail)
        [works_at] = profile.relations
        assert (works_at.type, works_at.direction, works_at.other_id) == ("works_at", "in", jane)
        assert works_at.other_name == "Jane Example"
        assert [lead.item_id for lead in profile.leads] == [item]

        by_project = await api.get(f"/api/graph?project_id={project_id}", GraphOut)
        assert {e.label for e in by_project.edges} == {"target"}
        around_jane = await api.get(f"/api/graph?entity_id={jane}", GraphOut)
        assert {n.id for n in around_jane.nodes} == {f"e{jane}", f"e{acme}", f"i{item}"}
        everything = await api.get("/api/graph", GraphOut)
        assert {n.id for n in everything.nodes} == {f"e{acme}", f"e{jane}", f"e{twin}", f"i{item}"}
        assert {e.label for e in everything.edges} == {"target", "works_at"}
        both = await api.client.get(f"/api/graph?project_id={project_id}&entity_id={jane}")
        assert both.status_code == 422

        [suggestion] = await api.ok_list("/api/merge-suggestions")
        kept = await api.ok("POST", f"/api/merge-suggestions/{suggestion['id']}/accept")
        assert kept["id"] == acme
        assert await api.ok_list("/api/merge-suggestions") == []

    async def test_full_graph_keeps_the_latest_entities_over_the_cap(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry_routes, "MAX_GRAPH_NODES", 2)
        with api.services.db.session() as session:
            for day, name in [(1, "Oldest"), (3, "Newest"), (2, "Middle")]:
                updated = datetime(2026, 11, day, tzinfo=UTC)
                session.add(Entity(kind=EntityKind.COMPANY, name=name, updated_at=updated))
        graph = await api.get("/api/graph", GraphOut)
        assert {n.label for n in graph.nodes} == {"Newest", "Middle"}


class TestDates:
    """A lead's date is its posting date, else the day a run found it; sightings date entities."""

    def seed(self, session: Session, project_id: int) -> dict[str, int]:
        def lead(key: str, score: float, posted: str | None) -> Item:
            fields: JsonObject = {"posted_on": {"value": posted}} if posted else {}
            return Item(
                project_id=project_id,
                kind=ItemKind.JOB,
                dedup_key=key,
                title=key,
                fields=fields,
                score_total=score,
                created_at=datetime(2026, 11, 14, tzinfo=UTC),
            )

        items = [
            lead("old", 90, "2026-10-01"),
            lead("new", 50, "2026-11-12"),
            lead("found", 70, None),
        ]
        old_co = Entity(kind=EntityKind.COMPANY, name="Old Co")
        new_co = Entity(kind=EntityKind.COMPANY, name="New Co")
        session.add_all([*items, old_co, new_co])
        session.flush()
        session.add_all(
            [
                ItemLink(item_id=items[0].id, entity_id=old_co.id, role="subject"),
                sighting(old_co.id, datetime(2026, 10, 1, tzinfo=UTC)),
                sighting(new_co.id, datetime(2026, 11, 13, tzinfo=UTC)),
            ]
        )
        return {i.dedup_key: i.id for i in items} | {"old_co": old_co.id, "new_co": new_co.id}

    async def test_report_window_and_date_sort(self, api: Api) -> None:
        project_id = await api.project(brief=biz_brief())
        with api.services.db.session() as session:
            ids = self.seed(session, project_id)
        url = f"/api/projects/{project_id}/items"

        by_score = await api.get(url, list[ItemOut])
        assert [(i.title, i.dated.isoformat()) for i in by_score] == [
            ("old", "2026-10-01"),
            ("found", "2026-11-14"),
            ("new", "2026-11-12"),
        ]
        recent = await api.get(f"{url}?since=2026-11-01&sort=date", list[ItemOut])
        assert [i.title for i in recent] == ["found", "new"]
        until = await api.get(f"{url}?until=2026-11-12", list[ItemOut])
        assert [i.title for i in until] == ["old", "new"]

        graph = await api.get(f"/api/graph?project_id={project_id}&since=2026-11-01", GraphOut)
        assert {n.id for n in graph.nodes} == {f"i{ids['new']}", f"i{ids['found']}"}

    async def test_registry_lists_entities_seen_since_a_date(self, api: Api) -> None:
        project_id = await api.project(brief=biz_brief())
        with api.services.db.session() as session:
            self.seed(session, project_id)

        seen = await api.get("/api/entities?seen_since=2026-11-01", list[EntityOut])

        assert [e.name for e in seen] == ["New Co"]


def sighting(entity_id: int, seen_at: datetime) -> Sighting:
    return Sighting(
        entity_id=entity_id,
        source_kind=SourceKind.WEB,
        source_url="https://a.example",
        seen_at=seen_at,
    )
