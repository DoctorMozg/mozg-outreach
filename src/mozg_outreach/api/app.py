"""The FastAPI app: services in the lifespan, error mapping, routes and the built UI."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import SecretStr, ValidationError

from mozg_outreach.agents import (
    AgentOutputError,
    AgentRunError,
    AgentRunner,
    PromptLibrary,
    RunnerSettings,
    StepRunner,
)
from mozg_outreach.api.routes import briefs, cvs, items, mail, projects, registry, runs, voice
from mozg_outreach.api.services import Services
from mozg_outreach.api.settings import Settings
from mozg_outreach.apply.browser import ApplyBrowser
from mozg_outreach.apply.desk import ApplyDesk
from mozg_outreach.browser import BrowserService
from mozg_outreach.browser.toolkit import TOOLKIT, BrowserToolkit
from mozg_outreach.mail import GmailMailbox, Mailbox, MailError
from mozg_outreach.mail.drafts import GmailDrafts, MailDraftError
from mozg_outreach.pipelines.biz.profile_search import BraveProfiles, ProfileFinder
from mozg_outreach.pipelines.biz.progress import StepError
from mozg_outreach.pipelines.biz.site_contacts import SiteReader, SiteSweep
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


def _brave(client: httpx.AsyncClient, key: SecretStr | None) -> ProfileFinder | None:
    if key is None:
        logger.info("no Brave Search key: LinkedIn lookups are left to the contact finder")
        return None
    return BraveProfiles(client, key)


def create_app(
    settings: Settings,
    runner: StepRunner | None = None,
    adapters: AdapterFactory | None = None,
    applications: ApplyDesk | None = None,
    mailbox: Mailbox | None = None,
    sites: SiteReader | None = None,
    profiles: ProfileFinder | None = None,
) -> FastAPI:
    """Build the app; tests pass a recorded runner, fake feeds, browser, mailbox and lookups."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(settings.database)
        db.migrate()
        async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=30) as client:
            context = AdapterContext(client=client, forbidden_root=settings.workspaces)

            def feeds(kind: str, config: JsonObject) -> SourceAdapter:
                return build_adapter(kind, config, context)

            browser = BrowserService(settings.browser)
            agent_runner = runner or AgentRunner(
                RunnerSettings(max_concurrency=settings.max_parallel_agents),
                toolkits={TOOLKIT: BrowserToolkit(browser)},
            )
            prompts = PromptLibrary(settings.prompts)
            factory = adapters or feeds
            apply_browser = ApplyBrowser(
                settings.home / "browser-profile", settings.browser.channel
            )
            manager = RunManager(
                RunEnvironment(
                    db=db,
                    runner=agent_runner,
                    prompts=prompts,
                    workspaces=settings.workspaces,
                    adapters=factory,
                    max_parallel_runs=settings.max_parallel_runs,
                    applications=applications or apply_browser,
                    sites=sites or SiteSweep(client),
                    profiles=profiles or _brave(client, settings.brave_api_key),
                ),
                FLOWS,
            )
            manager.recover()
            gmail_box = mailbox or (GmailMailbox(settings.gmail) if settings.gmail else None)
            gmail = GmailDrafts(db, gmail_box) if gmail_box else None
            app.state.services = Services(
                db=db,
                runs=manager,
                runner=agent_runner,
                prompts=prompts,
                adapters=factory,
                home=settings.home,
                browser=browser,
                gmail=gmail,
            )
            logger.info("serving data from %s", settings.home)
            # A test's mailbox is checked by hand, never by the clock.
            poller = asyncio.create_task(gmail.watch()) if gmail and not mailbox else None
            try:
                yield
            finally:
                if poller is not None:
                    poller.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await poller
                await manager.shutdown()
                await apply_browser.stop()
                await browser.stop()
                db.engine.dispose()

    app = FastAPI(title="mozg-outreach", lifespan=lifespan)
    app.add_exception_handler(LookupError, _error(404))
    app.add_exception_handler(RunStateError, _error(409))
    app.add_exception_handler(FieldSpecConflictError, _error(409))
    app.add_exception_handler(ValidationError, _error(422))
    # A failed agent call in a request (voice profile, brief draft) is an upstream failure.
    app.add_exception_handler(AgentRunError, _error(502))
    app.add_exception_handler(AgentOutputError, _error(502))
    app.add_exception_handler(MailDraftError, _error(409))
    app.add_exception_handler(StepError, _error(409))
    app.add_exception_handler(MailError, _error(502))
    for module in (projects, briefs, runs, items, cvs, voice, registry, mail):
        app.include_router(module.router)
    if settings.ui_dist.is_dir():
        app.mount("/", StaticFiles(directory=settings.ui_dist, html=True), name="ui")
    return app
