"""Settings edited on the Settings page; environment config lives in api/settings.py."""

import logging

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from mozg_outreach.storage.models import AppSettings

logger = logging.getLogger(__name__)

SETTINGS_ROW = 1


class UserSettings(BaseModel):
    auto_enrich_min_score: int | None = Field(
        default=80,
        ge=0,
        le=100,
        description="Leads a search scores at least this high are enriched right after it; "
        "null turns automatic enrichment off.",
    )


class SettingsStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def load(self) -> UserSettings:
        row = self._session.get(AppSettings, SETTINGS_ROW)
        # Stored values override the defaults, so a setting added later starts at its default.
        return UserSettings.model_validate(row.values if row else {})

    def save(self, settings: UserSettings) -> UserSettings:
        row = self._session.get(AppSettings, SETTINGS_ROW) or AppSettings(id=SETTINGS_ROW)
        row.values = settings.model_dump(mode="json")
        self._session.add(row)
        self._session.flush()
        logger.info("settings saved: %s", row.values)
        return settings
