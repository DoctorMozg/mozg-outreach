import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mozg_outreach.sources.models import SourceQuery
from mozg_outreach.sources.telegram import (
    ChannelMessage,
    SessionStore,
    TelegramAdapter,
    TelegramConfig,
    TelegramConfigError,
    TelegramCredentials,
    TelegramReadError,
    channel_username,
)

SECRET_HASH = "0123456789abcdef0123456789abcdef"


class FakeSession:
    """Serves synthetic channel history and honours min_id like Telethon does."""

    def __init__(self, history: dict[str, list[ChannelMessage]], session_file: Path) -> None:
        self.history = history
        self.session_file = session_file
        self.calls: list[tuple[str, int, int]] = []
        self.connected = False

    async def connect(self) -> None:
        self.connected = True
        self.session_file.write_text("sqlite")  # Telethon creates it with default modes
        self.session_file.chmod(0o644)

    async def disconnect(self) -> None:
        self.connected = False

    async def messages(self, channel: str, min_id: int, limit: int) -> list[ChannelMessage]:
        self.calls.append((channel, min_id, limit))
        if channel not in self.history:
            raise TelegramReadError(f"ChannelPrivateError reading {channel}")
        fresh = [m for m in self.history[channel] if m.id > min_id]
        return sorted(fresh, key=lambda m: m.id, reverse=min_id == 0)[:limit]


def message(msg_id: int, text: str) -> ChannelMessage:
    return ChannelMessage(id=msg_id, text=text, date=datetime(2026, 11, msg_id, tzinfo=UTC))


@pytest.fixture
def session_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "state" / "telegram"
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", SECRET_HASH)
    monkeypatch.setenv("MOZG_TELEGRAM_SESSION", str(base))
    return base


@pytest.fixture
def history() -> dict[str, list[ChannelMessage]]:
    return {
        "remote_python_jobs": [
            message(1, "Senior Python dev wanted, remote\nApply by DM"),
            message(2, "Rust gig for two weeks"),
            message(3, "   "),
            message(4, "Python freelance: data pipeline"),
        ]
    }


def adapter_for(
    history: dict[str, list[ChannelMessage]], session_base: Path, **config: object
) -> tuple[TelegramAdapter, list[FakeSession]]:
    sessions: list[FakeSession] = []

    def factory(credentials: TelegramCredentials, path: Path) -> FakeSession:
        session = FakeSession(history, path.with_name(path.name + ".session"))
        sessions.append(session)
        return session

    cfg = TelegramConfig.model_validate({"channels": ["@remote_python_jobs"], **config})
    return TelegramAdapter(cfg, session_factory=factory), sessions


class TestFetch:
    async def test_messages_become_items_with_public_urls(
        self, session_base: Path, history: dict[str, list[ChannelMessage]]
    ) -> None:
        adapter, _ = adapter_for(history, session_base, kind="job")

        result = await adapter.fetch(SourceQuery(), None)

        assert {i.source_item_id for i in result.items} == {
            "remote_python_jobs/1",
            "remote_python_jobs/2",
            "remote_python_jobs/4",
        }  # the blank message is dropped
        first = next(i for i in result.items if i.source_item_id.endswith("/1"))
        assert first.url == "https://t.me/remote_python_jobs/1"
        assert first.kind == "job"
        assert first.title == "Senior Python dev wanted, remote"
        assert first.license_note
        assert "AI training" in first.license_note

    async def test_keywords_from_config_and_query_filter(
        self, session_base: Path, history: dict[str, list[ChannelMessage]]
    ) -> None:
        adapter, _ = adapter_for(history, session_base, keywords=["python"])

        result = await adapter.fetch(SourceQuery(), None)

        assert {i.source_item_id for i in result.items} == {
            "remote_python_jobs/1",
            "remote_python_jobs/4",
        }

    async def test_cursor_round_trip_reads_only_new_messages(
        self, session_base: Path, history: dict[str, list[ChannelMessage]]
    ) -> None:
        adapter, sessions = adapter_for(history, session_base)
        first = await adapter.fetch(SourceQuery(), None)
        assert json.loads(first.cursor or "") == {"remote_python_jobs": 4}

        history["remote_python_jobs"].append(message(5, "New Python role"))
        second = await adapter.fetch(SourceQuery(), first.cursor)

        assert [i.source_item_id for i in second.items] == ["remote_python_jobs/5"]
        assert sessions[1].calls == [("remote_python_jobs", 4, 100)]
        assert json.loads(second.cursor or "") == {"remote_python_jobs": 5}

    async def test_cursor_advances_past_messages_the_keywords_filtered_out(
        self, session_base: Path, history: dict[str, list[ChannelMessage]]
    ) -> None:
        adapter, _ = adapter_for(history, session_base)

        result = await adapter.fetch(SourceQuery(keywords=["nothing-matches"]), None)

        assert result.items == []
        assert json.loads(result.cursor or "") == {"remote_python_jobs": 4}

    async def test_unreadable_channel_is_reported_and_others_continue(
        self, session_base: Path, history: dict[str, list[ChannelMessage]]
    ) -> None:
        adapter, _ = adapter_for(
            history, session_base, channels=["secret_club", "remote_python_jobs"]
        )

        result = await adapter.fetch(SourceQuery(), None)

        assert len(result.items) == 3
        assert result.errors == ["telegram: ChannelPrivateError reading secret_club"]

    async def test_invalid_cursor_is_reported_and_ignored(
        self, session_base: Path, history: dict[str, list[ChannelMessage]]
    ) -> None:
        adapter, _ = adapter_for(history, session_base)

        result = await adapter.fetch(SourceQuery(), "{not json")

        assert len(result.items) == 3
        assert result.errors == ["telegram: ignored invalid cursor"]

    async def test_payload_and_items_never_contain_credentials(
        self, session_base: Path, history: dict[str, list[ChannelMessage]]
    ) -> None:
        adapter, _ = adapter_for(history, session_base)

        result = await adapter.fetch(SourceQuery(), None)

        dumped = result.model_dump_json()
        assert SECRET_HASH not in dumped
        assert "12345" not in dumped

    async def test_session_file_is_locked_down_after_use(
        self, session_base: Path, history: dict[str, list[ChannelMessage]]
    ) -> None:
        adapter, _ = adapter_for(history, session_base)

        await adapter.fetch(SourceQuery(), None)

        session_file = session_base.with_name("telegram.session")
        assert stat.S_IMODE(session_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(session_base.parent.stat().st_mode) == 0o700

    async def test_disconnects_when_a_read_blows_up(self, session_base: Path) -> None:
        class Boom(FakeSession):
            async def messages(self, channel: str, min_id: int, limit: int) -> list[ChannelMessage]:
                raise RuntimeError("bug")

        sessions: list[Boom] = []

        def factory(credentials: TelegramCredentials, path: Path) -> Boom:
            sessions.append(Boom({}, path.with_name(path.name + ".session")))
            return sessions[-1]

        adapter = TelegramAdapter(TelegramConfig(channels=["any_channel"]), session_factory=factory)

        with pytest.raises(RuntimeError, match="bug"):
            await adapter.fetch(SourceQuery(), None)
        assert not sessions[0].connected


class TestCredentialsAndSession:
    @pytest.mark.parametrize("missing", ["TELEGRAM_API_ID", "TELEGRAM_API_HASH"])
    async def test_missing_env_var_error_names_the_variable(
        self,
        missing: str,
        session_base: Path,
        history: dict[str, list[ChannelMessage]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv(missing)
        adapter, _ = adapter_for(history, session_base)

        with pytest.raises(TelegramConfigError, match=missing):
            await adapter.fetch(SourceQuery(), None)

    def test_non_numeric_api_id_error_does_not_echo_the_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TELEGRAM_API_ID", "abc-secret")
        monkeypatch.setenv("TELEGRAM_API_HASH", SECRET_HASH)

        with pytest.raises(TelegramConfigError) as raised:
            TelegramCredentials.from_env()

        assert "TELEGRAM_API_ID" in str(raised.value)
        assert "abc-secret" not in str(raised.value)

    def test_credentials_repr_hides_the_hash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TELEGRAM_API_ID", "1")
        monkeypatch.setenv("TELEGRAM_API_HASH", SECRET_HASH)

        assert SECRET_HASH not in repr(TelegramCredentials.from_env())

    async def test_session_inside_forbidden_root_is_refused(
        self,
        tmp_path: Path,
        history: dict[str, list[ChannelMessage]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        workspace = tmp_path / "workspaces"
        monkeypatch.setenv("TELEGRAM_API_ID", "1")
        monkeypatch.setenv("TELEGRAM_API_HASH", SECRET_HASH)
        monkeypatch.setenv("MOZG_TELEGRAM_SESSION", str(workspace / "p1" / "tg"))
        adapter = TelegramAdapter(
            TelegramConfig(channels=["any_channel"]), forbidden_root=workspace
        )

        with pytest.raises(TelegramConfigError, match="inside the workspace root"):
            await adapter.fetch(SourceQuery(), None)
        assert not workspace.exists()

    def test_session_outside_forbidden_root_is_accepted(self, tmp_path: Path) -> None:
        store = SessionStore(tmp_path / "safe" / "tg", forbidden_root=tmp_path / "workspaces")
        store.prepare()
        assert (tmp_path / "safe").is_dir()

    def test_default_session_location_is_under_the_user_data_dir(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MOZG_TELEGRAM_SESSION", raising=False)
        store = SessionStore.from_env()
        assert store.session_file == Path.home() / ".local/share/mozg-outreach/telegram.session"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@jobs_chan", "jobs_chan"),
        ("https://t.me/jobs_chan", "jobs_chan"),
        ("jobs_chan/", "jobs_chan"),
    ],
)
def test_channel_username_normalization(raw: str, expected: str) -> None:
    assert channel_username(raw) == expected
