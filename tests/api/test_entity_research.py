from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from mozg_outreach.domain import EntityKind, IdentifierKind
from mozg_outreach.registry.identifiers import normalize
from mozg_outreach.registry.registry import EntityRegistry, Observation
from tests.api.conftest import Api, open_api
from tests.pipelines.conftest import RecordedRunner
from tests.pipelines.entities.test_research import WRONG, check, findings


@pytest.fixture
async def api(tmp_path: Path) -> AsyncIterator[Api]:
    async for opened in open_api(tmp_path, RecordedRunner({})):
        yield opened


async def jane(api: Api) -> int:
    await api.project("seeker")
    with api.services.db.session() as session:
        linkedin = normalize(IdentifierKind.LINKEDIN, WRONG)
        assert linkedin is not None
        entity = EntityRegistry(session).observe(
            Observation(kind=EntityKind.PERSON, name="Jane Doe", identifiers=[linkedin])
        )
        return entity.id


class TestSearchAgain:
    async def test_a_wrong_linkedin_is_replaced_and_shown_as_removed(self, api: Api) -> None:
        entity_id = await jane(api)
        api.script({"entity-researcher": [findings(checks=[check("someone_else")])]})

        run = await api.ok(
            "POST", f"/api/entities/{entity_id}/research", {"note": " It's another Jane. "}
        )
        done = await api.settle(int(str(run["id"])))
        detail = await api.ok("GET", f"/api/entities/{entity_id}")

        assert done["status"] == "complete", done
        assert done["state"]["note"] == "It's another Jane."  # type: ignore[index]
        assert detail["identifiers"] == [
            {"kind": "linkedin", "value": "linkedin.com/in/jane-doe-acme"}
        ]
        rejected = detail["rejected"]
        assert isinstance(rejected, list)
        assert [(r["kind"], r["value"]) for r in rejected] == [("linkedin", WRONG)]
        payload = api.runner.payloads["entity-researcher"][0]
        assert payload.model_dump()["note"] == "It's another Jane."

    async def test_an_unknown_entity_is_404(self, api: Api) -> None:
        response = await api.client.post("/api/entities/999/research")

        assert response.status_code == 404

    async def test_no_project_means_nowhere_to_run(self, api: Api) -> None:
        with api.services.db.session() as session:
            entity = EntityRegistry(session).observe(
                Observation(kind=EntityKind.COMPANY, name="Acme")
            )
            entity_id = entity.id

        response = await api.client.post(f"/api/entities/{entity_id}/research")

        assert response.status_code == 409
