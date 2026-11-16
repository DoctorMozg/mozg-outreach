import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from pydantic import TypeAdapter

from mozg_outreach.api.app import create_app
from mozg_outreach.api.services import Services
from mozg_outreach.api.settings import Settings
from mozg_outreach.mail import Mailbox
from mozg_outreach.pipelines.biz.models import Strategy
from mozg_outreach.pipelines.fields import FieldProposal
from mozg_outreach.storage.models import JsonObject
from mozg_outreach.storage.repositories import FieldSpecRepository
from tests.pipelines.biz.conftest import FIXTURES, load_fixture
from tests.pipelines.conftest import REPO_PROMPTS, RecordedRunner, Scripted
from tests.runs.conftest import FakeFeed


class Api:
    """The app over an in-process transport, with the scripted runner behind it."""

    def __init__(self, app: FastAPI, client: httpx.AsyncClient, runner: RecordedRunner) -> None:
        self.app = app
        self.client = client
        self.runner = runner

    @property
    def services(self) -> Services:
        found: Services = self.app.state.services
        return found

    def script(self, script: dict[str, list[Scripted]]) -> None:
        for name, results in script.items():
            self.runner._script.setdefault(name, []).extend(results)

    async def settle(self, run_id: int) -> JsonObject:
        await self.services.runs.wait(run_id)
        return await self.ok("GET", f"/api/runs/{run_id}")

    async def ok(self, method: str, url: str, body: object = None) -> JsonObject:
        payload = await self._call(method, url, body)
        assert isinstance(payload, dict), payload
        return payload

    async def get[TModel](self, url: str, model: type[TModel]) -> TModel:
        return TypeAdapter(model).validate_python(await self._call("GET", url, None))

    async def ok_list(self, url: str) -> list[JsonObject]:
        payload = await self._call("GET", url, None)
        assert isinstance(payload, list), payload
        return payload

    async def _call(self, method: str, url: str, body: object) -> object:
        response = await self.client.request(method, url, json=body)
        assert response.is_success, response.text
        payload: object = response.json() if response.content else {}
        return payload

    async def project(self, mode: str = "biz", brief: JsonObject | None = None) -> int:
        created = await self.ok(
            "POST",
            "/api/projects",
            {"name": "DACH SaaS", "mode": mode, "sender_name": "Stan Petrov", "brief": brief},
        )
        return int(str(created["id"]))

    async def biz_project(self) -> int:
        """A biz project with a brief and accepted report fields, so a search may start."""
        project_id = await self.project(brief=biz_brief())
        spec = load_fixture("field_proposal", FieldProposal).spec
        with self.services.db.session() as session:
            fields = FieldSpecRepository(session)
            fields.accept(project_id, fields.propose(project_id, spec).version)
        return project_id


def biz_brief() -> JsonObject:
    loaded: JsonObject = json.loads((FIXTURES / "brief.json").read_text())
    return loaded


async def open_api(
    tmp_path: Path, runner: RecordedRunner, mailbox: Mailbox | None = None
) -> AsyncIterator[Api]:
    settings = Settings(home=tmp_path / "home", prompts=REPO_PROMPTS, ui_dist=tmp_path / "no-ui")
    feed = FakeFeed()
    app = create_app(settings, runner=runner, adapters=lambda _kind, _config: feed, mailbox=mailbox)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield Api(app, client, runner)


@pytest.fixture
async def api(tmp_path: Path) -> AsyncIterator[Api]:
    async for opened in open_api(tmp_path, RecordedRunner({})):
        yield opened


@pytest.fixture
def strategy() -> Strategy:
    return load_fixture("strategy", Strategy)


def sse_events(text: str) -> list[JsonObject]:
    return [json.loads(line[5:]) for line in text.splitlines() if line.startswith("data:")]
