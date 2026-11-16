import os
from pathlib import Path

import pytest

from mozg_outreach.api import __main__ as entry
from mozg_outreach.api.settings import Settings
from mozg_outreach.mail.models import ADDRESS_VAR, PASSWORD_VAR


class Started:
    port: int | None = None
    settings: Settings | None = None

    @property
    def gmail(self) -> str | None:
        assert self.settings is not None
        return self.settings.gmail.address if self.settings.gmail else None


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Started:
    """Runs from an empty folder with a private environment and no real server."""
    found = Started()
    monkeypatch.setattr(os, "environ", {"MOZG_OUTREACH_HOME": str(tmp_path / "home")})
    monkeypatch.chdir(tmp_path)

    def run(app: Settings, host: str, port: int) -> None:
        found.settings, found.port = app, port

    monkeypatch.setattr(entry, "create_app", lambda settings: settings)
    monkeypatch.setattr("mozg_outreach.api.__main__.uvicorn.run", run)
    return found


def test_reads_the_env_file_in_the_working_folder(started: Started, tmp_path: Path) -> None:
    (tmp_path / entry.ENV_FILE).write_text(
        "MOZG_OUTREACH_PORT=8799\n"
        f"{ADDRESS_VAR}=me@example.com\n"
        f'{PASSWORD_VAR}="abcd efgh ijkl mnop"\n'
    )

    entry.main()

    assert (started.port, started.gmail) == (8799, "me@example.com")
    assert started.settings is not None
    assert started.settings.gmail is not None
    assert started.settings.gmail.app_password.get_secret_value() == "abcdefghijklmnop"


def test_the_shell_wins_over_the_file(started: Started, tmp_path: Path) -> None:
    os.environ["MOZG_OUTREACH_PORT"] = "8743"
    (tmp_path / entry.ENV_FILE).write_text("MOZG_OUTREACH_PORT=8799\n")

    entry.main()

    assert started.port == 8743


def test_no_file_is_fine(started: Started) -> None:
    entry.main()

    assert (started.port, started.gmail) == (8742, None)
