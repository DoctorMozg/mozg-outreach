"""Where the service keeps its data, read once from the environment at startup."""

import os
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr

from mozg_outreach.browser import BrowserSettings
from mozg_outreach.mail import GmailSettings

REPO_ROOT = Path(__file__).resolve().parents[3]
BRAVE_KEY = "MOZG_OUTREACH_BRAVE_API_KEY"


class Settings(BaseModel):
    home: Path
    prompts: Path = REPO_ROOT / "prompts"
    ui_dist: Path = REPO_ROOT / "ui" / "dist"
    max_parallel_runs: int = Field(default=2, ge=1)
    max_parallel_agents: int = Field(default=3, ge=1)
    browser: BrowserSettings = Field(default_factory=BrowserSettings)
    # None leaves Gmail off: no draft pushing and no polling.
    gmail: GmailSettings | None = None
    # None leaves LinkedIn lookups to the contact finder's own web search.
    brave_api_key: SecretStr | None = None

    @property
    def database(self) -> Path:
        return self.home / "outreach.db"

    @property
    def workspaces(self) -> Path:
        return self.home / "workspaces"

    @classmethod
    def from_env(cls) -> "Settings":
        default = Path.home() / ".local" / "share" / "mozg-outreach"
        home = Path(os.environ.get("MOZG_OUTREACH_HOME", default)).expanduser()
        runs = os.environ.get("MOZG_OUTREACH_MAX_RUNS")
        # "chromium" uses Playwright's own build where Google Chrome isn't installed.
        channel = os.environ.get("MOZG_OUTREACH_BROWSER", "chrome")
        return cls(
            home=home,
            max_parallel_runs=int(runs) if runs else 2,
            browser=BrowserSettings.model_validate({"channel": channel}),
            gmail=GmailSettings.from_env(),
            brave_api_key=SecretStr(brave) if (brave := os.environ.get(BRAVE_KEY)) else None,
        )
