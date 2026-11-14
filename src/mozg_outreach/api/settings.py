"""Where the service keeps its data, read once from the environment at startup."""

import os
from pathlib import Path

from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseModel):
    home: Path
    prompts: Path = REPO_ROOT / "prompts"
    ui_dist: Path = REPO_ROOT / "ui" / "dist"
    max_parallel_runs: int = Field(default=2, ge=1)
    max_parallel_agents: int = Field(default=3, ge=1)

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
        return cls(home=home, max_parallel_runs=int(runs) if runs else 2)
