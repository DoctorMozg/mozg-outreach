from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from mozg_outreach.api.schemas import EntityDetail, EntityOut, GraphOut, ItemOut
from mozg_outreach.domain import EntityKind, ItemKind, SourceKind
from mozg_outreach.naturalize.voice import VoiceNotes
from mozg_outreach.pipelines.biz.models import Strategy
from mozg_outreach.storage.models import (
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

    async def test_search_needs_a_brief(self, api: Api) -> None:
        project_id = await api.project()
        assert (
            await api.client.post(f"/api/projects/{project_id}/runs", json={})
        ).status_code == 409


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
        assert kinds == ["web_search", "greenhouse"]
        assert added["cursor"] is None

    async def test_only_the_same_item_waits_for_its_open_run(self, api: Api) -> None:
        project_id = await api.project("seeker")
        with api.services.db.session() as session:
            rows = [
                Item(project_id=project_id, kind=ItemKind.JOB, dedup_key=k, title=k, selected=True)
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

    async def test_item_actions_need_selection_and_a_pipeline(self, api: Api) -> None:
        project_id = await api.project("seeker")
        with api.services.db.session() as session:
            item = Item(project_id=project_id, kind=ItemKind.GIG, dedup_key="g", title="Gig")
            session.add(item)
            session.flush()
            item_id = item.id
        assert (await api.client.post(f"/api/items/{item_id}/letters")).status_code == 409
        await api.ok("PATCH", f"/api/items/{item_id}", {"selected": True})
        assert (await api.client.post(f"/api/items/{item_id}/enrich")).status_code == 409
        detail = await api.ok("GET", f"/api/items/{item_id}")
        assert detail["selected"] is True
        assert detail["drafts"] == []


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


class TestRegistry:
    def seed(self, session: Session, project_id: int) -> tuple[int, int, int]:
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
        return acme.id, jane.id, item.id

    async def test_profile_graph_and_merge(self, api: Api) -> None:
        project_id = await api.project(brief=biz_brief())
        with api.services.db.session() as session:
            acme, jane, item = self.seed(session, project_id)

        profile = await api.get(f"/api/entities/{acme}", EntityDetail)
        [works_at] = profile.relations
        assert (works_at.type, works_at.direction, works_at.other_id) == ("works_at", "in", jane)
        assert works_at.other_name == "Jane Example"
        assert [lead.item_id for lead in profile.leads] == [item]

        by_project = await api.get(f"/api/graph?project_id={project_id}", GraphOut)
        assert {e.label for e in by_project.edges} == {"target"}
        around_jane = await api.get(f"/api/graph?entity_id={jane}", GraphOut)
        assert {n.id for n in around_jane.nodes} == {f"e{jane}", f"e{acme}", f"i{item}"}
        assert (await api.client.get("/api/graph")).status_code == 422

        [suggestion] = await api.ok_list("/api/merge-suggestions")
        kept = await api.ok("POST", f"/api/merge-suggestions/{suggestion['id']}/accept")
        assert kept["id"] == acme
        assert await api.ok_list("/api/merge-suggestions") == []


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
