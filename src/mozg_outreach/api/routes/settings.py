"""Settings shared by every project, edited on the Settings page."""

from typing import Annotated

from fastapi import APIRouter, Depends

from mozg_outreach.api.services import Services, services
from mozg_outreach.settings.store import SettingsStore, UserSettings

router = APIRouter(prefix="/api")
Svc = Annotated[Services, Depends(services)]


@router.get("/settings")
async def get_settings(svc: Svc) -> UserSettings:
    with svc.db.session() as session:
        return SettingsStore(session).load()


@router.put("/settings")
async def put_settings(body: UserSettings, svc: Svc) -> UserSettings:
    with svc.db.session() as session:
        return SettingsStore(session).save(body)
