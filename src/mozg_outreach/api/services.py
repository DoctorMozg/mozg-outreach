"""What every route needs, built once in the app lifespan and read from app.state."""

from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from mozg_outreach.agents import PromptLibrary, StepRunner
from mozg_outreach.browser import BrowserService
from mozg_outreach.mail.drafts import GmailDrafts
from mozg_outreach.runs.flow import AdapterFactory
from mozg_outreach.runs.manager import RunManager
from mozg_outreach.storage.db import Database
from mozg_outreach.storage.models import Base


@dataclass(frozen=True)
class Services:
    db: Database
    runs: RunManager
    runner: StepRunner
    prompts: PromptLibrary
    adapters: AdapterFactory
    home: Path
    browser: BrowserService
    gmail: GmailDrafts | None


def services(request: Request) -> Services:
    found: Services = request.app.state.services
    return found


def get_or_404[TRow: Base](session: Session, model: type[TRow], row_id: int) -> TRow:
    row = session.get(model, row_id)
    if row is None:
        raise HTTPException(404, f"no {model.__tablename__.removesuffix('s')} {row_id}")
    return row
