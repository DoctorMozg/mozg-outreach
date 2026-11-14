import json
import logging
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field, SecretStr, TypeAdapter, ValidationError

from mozg_outreach.sources.base import AdapterConfig, matches_keywords, select_items
from mozg_outreach.sources.models import FetchResult, ItemKind, SourceItem, SourceQuery

logger = logging.getLogger(__name__)

API_ID_VAR = "TELEGRAM_API_ID"
API_HASH_VAR = "TELEGRAM_API_HASH"
SESSION_VAR = "MOZG_TELEGRAM_SESSION"
DEFAULT_SESSION = "~/.local/share/mozg-outreach/telegram"

LICENSE_NOTE = (
    "Telegram API terms forbid scraping for AI training; reads are limited to channels the "
    "user chose."
)
_PUBLIC_USERNAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{3,31}")
_CURSOR = TypeAdapter(dict[str, int])


class TelegramConfigError(ValueError):
    """Missing or malformed Telegram credentials or session location."""


class TelegramReadError(RuntimeError):
    """A channel could not be read (private, unknown, flood-limited)."""


class TelegramCredentials(BaseModel):
    api_id: int
    api_hash: SecretStr

    @classmethod
    def from_env(cls) -> "TelegramCredentials":
        env = os.environ
        raw_id = env.get(API_ID_VAR, "").strip()
        raw_hash = env.get(API_HASH_VAR, "").strip()
        if not raw_id:
            raise TelegramConfigError(f"environment variable {API_ID_VAR} is not set")
        if not raw_hash:
            raise TelegramConfigError(f"environment variable {API_HASH_VAR} is not set")
        if not raw_id.isdecimal():
            raise TelegramConfigError(f"environment variable {API_ID_VAR} must be a number")
        return cls(api_id=int(raw_id), api_hash=SecretStr(raw_hash))


class ChannelMessage(BaseModel):
    id: int
    text: str
    date: datetime
    author: str | None = None


class TelegramSession(Protocol):
    """What the adapter needs from a Telegram client; Telethon stays behind this."""

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def messages(self, channel: str, min_id: int, limit: int) -> list[ChannelMessage]: ...


type SessionFactory = Callable[[TelegramCredentials, Path], TelegramSession]


class SessionStore:
    """Locates the Telethon session file and keeps it private to the user."""

    def __init__(self, base_path: Path, forbidden_root: Path | None = None) -> None:
        self.base_path = base_path
        self._forbidden_root = forbidden_root

    @classmethod
    def from_env(cls, forbidden_root: Path | None = None) -> "SessionStore":
        location = os.environ.get(SESSION_VAR) or DEFAULT_SESSION
        return cls(Path(location).expanduser(), forbidden_root)

    @property
    def session_file(self) -> Path:
        # Telethon appends .session unless the name already ends with it.
        if self.base_path.name.endswith(".session"):
            return self.base_path
        return self.base_path.with_name(self.base_path.name + ".session")

    def prepare(self) -> None:
        self._refuse_forbidden_location()
        parent = self.base_path.parent
        if not parent.exists():
            parent.mkdir(parents=True, mode=0o700)
            parent.chmod(0o700)
        self.secure()

    def secure(self) -> None:
        """Telethon creates the file on connect, so this runs before and after use."""
        if self.session_file.exists():
            self.session_file.chmod(0o600)

    def _refuse_forbidden_location(self) -> None:
        if self._forbidden_root is None:
            return
        target = self.session_file.resolve()
        if target.is_relative_to(self._forbidden_root.resolve()):
            raise TelegramConfigError(
                f"{SESSION_VAR} points inside the workspace root {self._forbidden_root}; "
                "the session grants full account access"
            )


class TelethonSession:
    def __init__(self, credentials: TelegramCredentials, session_path: Path) -> None:
        # Imported lazily: telethon is untyped and only this module's real path needs it.
        from telethon import TelegramClient

        self._client = TelegramClient(
            str(session_path), credentials.api_id, credentials.api_hash.get_secret_value()
        )

    async def connect(self) -> None:
        await self._client.connect()
        if not await self._client.is_user_authorized():
            raise TelegramConfigError(
                "Telegram session is not logged in; "
                "run `python -m mozg_outreach.sources.telegram_login`"
            )

    async def disconnect(self) -> None:
        await self._client.disconnect()

    async def messages(self, channel: str, min_id: int, limit: int) -> list[ChannelMessage]:
        from telethon.errors import RPCError

        # With a cursor, read forward from it so a limit never skips the gap.
        try:
            raw = [
                message
                async for message in self._client.iter_messages(
                    channel, min_id=min_id, limit=limit, reverse=min_id > 0
                )
            ]
        except (RPCError, ValueError) as exc:
            raise TelegramReadError(f"{type(exc).__name__} reading {channel}") from exc
        return [
            ChannelMessage(
                id=message.id,
                text=message.message or "",
                date=message.date,
                author=message.post_author,
            )
            for message in raw
        ]


class TelegramConfig(AdapterConfig):
    channels: list[str] = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)
    kind: Literal["job", "gig", "signal"] = "signal"


def channel_username(channel: str) -> str:
    return re.sub(r"^(https?://)?(t\.me/)?@?", "", channel.strip()).strip("/")


class TelegramAdapter:
    name = "telegram"

    def __init__(
        self,
        config: TelegramConfig,
        *,
        forbidden_root: Path | None = None,
        session_factory: SessionFactory = TelethonSession,
    ) -> None:
        self._config = config
        self._forbidden_root = forbidden_root
        self._session_factory = session_factory

    async def fetch(self, query: SourceQuery, cursor: str | None) -> FetchResult:
        errors: list[str] = []
        positions = self._read_cursor(cursor, errors)
        credentials = TelegramCredentials.from_env()
        store = SessionStore.from_env(self._forbidden_root)
        store.prepare()

        session = self._session_factory(credentials, store.base_path)
        read: list[SourceItem] = []
        try:
            await session.connect()
            for raw_channel in self._config.channels:
                channel = channel_username(raw_channel)
                try:
                    messages = await session.messages(
                        channel, positions.get(channel, 0), query.limit
                    )
                except TelegramReadError as exc:
                    errors.append(f"telegram: {exc}")
                    logger.warning("telegram: skipped channel %s: %s", channel, exc)
                    continue
                if messages:
                    positions[channel] = max(positions.get(channel, 0), *(m.id for m in messages))
                logger.info("telegram: %s returned %d messages", channel, len(messages))
                read.extend(self._to_items(channel, messages))
        finally:
            await session.disconnect()
            store.secure()

        keywords = [*query.keywords, *self._config.keywords]
        wanted = query.model_copy(update={"keywords": keywords})
        return FetchResult(
            items=select_items([i for i in read if matches_keywords(i, keywords)], wanted),
            cursor=json.dumps(positions, sort_keys=True),
            errors=errors,
        )

    def _read_cursor(self, cursor: str | None, errors: list[str]) -> dict[str, int]:
        if cursor is None:
            return {}
        try:
            return _CURSOR.validate_json(cursor)
        except ValidationError:
            errors.append("telegram: ignored invalid cursor")
            return {}

    def _to_items(self, channel: str, messages: list[ChannelMessage]) -> list[SourceItem]:
        fetched_at = datetime.now(UTC)
        public = _PUBLIC_USERNAME.fullmatch(channel) is not None
        kind: ItemKind = self._config.kind
        return [
            SourceItem(
                source=self.name,
                source_item_id=f"{channel}/{message.id}",
                url=f"https://t.me/{channel}/{message.id}" if public else None,
                kind=kind,
                title=message.text.strip().split("\n", 1)[0][:200] or None,
                raw_text=message.text,
                raw_payload={"channel": channel, "message_id": message.id},
                published_at=message.date,
                fetched_at=fetched_at,
                author=message.author,
                org_name=None,
                tags=[],
                license_note=LICENSE_NOTE,
            )
            for message in messages
            if message.text.strip()
        ]
