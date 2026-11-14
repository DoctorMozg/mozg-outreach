"""The FastAPI app: services in the lifespan, error mapping, routes and the built UI."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from mozg_outreach.agents import AgentRunner, PromptLibrary, RunnerSettings, StepRunner
from mozg_outreach.api.routes import items, projects, registry, runs, voice
from mozg_outreach.api.services import Services
from mozg_outreach.api.settings import Settings
from mozg_outreach.runs.catalog import FLOWS
from mozg_outreach.runs.flow import AdapterFactory
from mozg_outreach.runs.manager import RunEnvironment, RunManager, RunStateError
from mozg_outreach.sources.base import SourceAdapter
from mozg_outreach.sources.factory import AdapterContext, build_adapter
from mozg_outreach.storage.db import Database
from mozg_outreach.storage.models import JsonObject
from mozg_outreach.storage.repositories import FieldSpecConflictError

logger = logging.getLogger(__name__)
USER_AGENT = "mozg-outreach/0.1 (local research tool)"


def _error(status: int) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    async def handle(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=status)

    return handle


def create_app(
    settings: Settings, runner: StepRunner | None = None, adapters: AdapterFactory | None = None
) -> FastAPI:
    """Build the app; tests pass a recorded runner and fake feeds instead of the real ones."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(settings.database)
        db.create_schema()
        async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=30) as client:
            context = AdapterContext(client=client, forbidden_root=settings.workspaces)

            def feeds(kind: str, config: JsonObject) -> SourceAdapter:
                return build_adapter(kind, config, context)

            agent_runner = runner or AgentRunner(
                RunnerSettings(max_concurrency=settings.max_parallel_agents)
            )
            prompts = PromptLibrary(settings.prompts)
            factory = adapters or feeds
            manager = RunManager(
                RunEnvironment(
                    db=db,
                    runner=agent_runner,
                    prompts=prompts,
                    workspaces=settings.workspaces,
                    adapters=factory,
                    max_parallel_runs=settings.max_parallel_runs,
                ),
                FLOWS,
            )
            manager.recover()
            app.state.services = Services(
                db=db,
                runs=manager,
                runner=agent_runner,
                prompts=prompts,
                adapters=factory,
                home=settings.home,
            )
            logger.info("serving data from %s", settings.home)
            try:
                yield
            finally:
                await manager.shutdown()
                db.engine.dispose()

    app = FastAPI(title="mozg-outreach", lifespan=lifespan)
    app.add_exception_handler(LookupError, _error(404))
    app.add_exception_handler(RunStateError, _error(409))
    app.add_exception_handler(FieldSpecConflictError, _error(409))
    app.add_exception_handler(ValidationError, _error(422))
    for module in (projects, runs, items, voice, registry):
        app.include_router(module.router)
    if settings.ui_dist.is_dir():
        app.mount("/", StaticFiles(directory=settings.ui_dist, html=True), name="ui")
    return app
