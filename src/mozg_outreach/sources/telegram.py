import json
import logging
import os
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field, SecretStr, TypeAdapter, ValidationError, model_validator

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
    links: list[str] = Field(
        default_factory=list, description="Visible URLs, links behind words and button links."
    )


class ChannelInfo(BaseModel):
    username: str
    title: str
    broadcast: bool = Field(description="A channel that only admins post to, not a group chat.")
    subscribers: int | None


class TelegramSession(Protocol):
    """What the adapter needs from a Telegram client; Telethon stays behind this."""

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def messages(self, channel: str, min_id: int, limit: int) -> list[ChannelMessage]: ...

    async def search_channels(self, query: str, limit: int) -> list[ChannelInfo]: ...


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
                links=message_links(message),
            )
            for message in raw
        ]

    async def search_channels(self, query: str, limit: int) -> list[ChannelInfo]:
        """Public chats whose title or username matches, like the app's global search."""
        from telethon import functions
        from telethon.errors import RPCError
        from telethon.tl.types import Channel

        try:
            found = await self._client(functions.contacts.SearchRequest(q=query, limit=limit))
        except RPCError as exc:
            raise TelegramReadError(f"{type(exc).__name__} searching {query!r}") from exc
        return [
            ChannelInfo(
                username=chat.username,
                title=chat.title,
                broadcast=bool(chat.broadcast),
                subscribers=chat.participants_count,
            )
            for chat in found.chats
            if isinstance(chat, Channel) and chat.username
        ]


class _LinkedMessage(Protocol):
    """The part of telethon's untyped Message that carries links."""

    message: str | None
    entities: list[object] | None
    reply_markup: object | None


def message_links(message: _LinkedMessage) -> list[str]:
    """Job posts mostly hide the vacancy link behind words, so the plain text alone loses it."""
    from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl

    # Entity offsets count UTF-16 code units, and an emoji takes two of them.
    units = (message.message or "").encode("utf-16-le")
    links: list[str] = []
    for entity in message.entities or []:
        if isinstance(entity, MessageEntityTextUrl):
            links.append(entity.url)
        elif isinstance(entity, MessageEntityUrl):
            start, end = 2 * entity.offset, 2 * (entity.offset + entity.length)
            links.append(units[start:end].decode("utf-16-le"))
    for row in getattr(message.reply_markup, "rows", None) or []:
        # URL buttons keep the link on their type (InlineButtonTypeUrl) in current API layers.
        urls = [getattr(getattr(button, "type", None), "url", None) for button in row.buttons]
        links += [url for url in urls if isinstance(url, str)]
    return list(dict.fromkeys(links))


class TelegramConfig(AdapterConfig):
    channels: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    kind: Literal["job", "gig", "signal"] = "signal"
    discover: bool = Field(default=False, description="Search Telegram for matching channels.")
    excluded: list[str] = Field(default_factory=list, description="Channels never proposed again.")
    max_channels: int = Field(default=15, ge=1, le=40)

    @model_validator(mode="after")
    def _has_channels_or_discovers(self) -> "TelegramConfig":
        if not self.channels and not self.discover:
            raise ValueError("list channels or set discover to true")
        return self


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

    @asynccontextmanager
    async def open_session(self) -> AsyncIterator[TelegramSession]:
        """A connected session; the session file is locked down again however the block ends."""
        credentials = TelegramCredentials.from_env()
        store = SessionStore.from_env(self._forbidden_root)
        store.prepare()
        session = self._session_factory(credentials, store.base_path)
        try:
            await session.connect()
            yield session
        finally:
            await session.disconnect()
            store.secure()

    async def fetch(self, query: SourceQuery, cursor: str | None) -> FetchResult:
        errors: list[str] = []
        positions = self._read_cursor(cursor, errors)
        read: list[SourceItem] = []
        read_ids: dict[str, list[int]] = {}
        async with self.open_session() as session:
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
                read_ids[channel] = [positions.get(channel, 0), *(m.id for m in messages)]
                if messages:
                    positions[channel] = max(positions.get(channel, 0), *(m.id for m in messages))
                logger.info("telegram: %s returned %d messages", channel, len(messages))
                read.extend(self._to_items(channel, messages))

        keywords = [*query.keywords, *self._config.keywords]
        # The limit already applied per channel while reading, so only sort and filter here.
        wanted = query.model_copy(update={"keywords": keywords, "limit": len(read) or 1})
        items = select_items([i for i in read if matches_keywords(i, keywords)], wanted)
        if query.total_limit is not None and len(items) > query.total_limit:
            items = self._trim_oldest(items, query.total_limit, positions, read_ids, errors)
        return FetchResult(items=items, cursor=json.dumps(positions, sort_keys=True), errors=errors)

    @staticmethod
    def _trim_oldest(
        items: list[SourceItem],
        keep: int,
        positions: dict[str, int],
        read_ids: dict[str, list[int]],
        errors: list[str],
    ) -> list[SourceItem]:
        """Keep the oldest posts; each cursor stops just before its first dropped post."""
        ordered = sorted(items, key=lambda i: i.published_at or datetime.min.replace(tzinfo=UTC))
        kept, dropped = ordered[:keep], ordered[keep:]
        for item in dropped:
            channel, message_id = (
                str(item.raw_payload["channel"]),
                int(str(item.raw_payload["message_id"])),
            )
            earlier = [m for m in read_ids[channel] if m < message_id]
            positions[channel] = min(positions[channel], max(earlier))
        errors.append(f"parsed the oldest {keep} of {len(items)} posts, the rest next run.")
        logger.info("telegram: kept %d of %d posts, cursor rewound for the rest", keep, len(items))
        return kept

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
                raw_payload={"channel": channel, "message_id": message.id, "links": message.links},
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
