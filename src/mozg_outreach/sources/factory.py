import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import BaseModel

from mozg_outreach.sources.ats import (
    AshbyAdapter,
    BoardsConfig,
    CompaniesConfig,
    GreenhouseAdapter,
    LeverAdapter,
)
from mozg_outreach.sources.base import SourceAdapter
from mozg_outreach.sources.feeds import (
    ArbeitnowAdapter,
    ArbeitnowConfig,
    RemoteOkAdapter,
    RemoteOkConfig,
    RemotiveAdapter,
    RemotiveConfig,
)
from mozg_outreach.sources.hn import HnConfig, HnHiringAdapter
from mozg_outreach.sources.telegram import TelegramAdapter, TelegramConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AdapterContext:
    client: httpx.AsyncClient
    # Telegram sessions must never live inside a project workspace.
    forbidden_root: Path | None = None


type Builder = Callable[[dict[str, object], AdapterContext], SourceAdapter]


def _http[TConfig: BaseModel](
    config_model: type[TConfig], adapter: Callable[[httpx.AsyncClient, TConfig], SourceAdapter]
) -> Builder:
    def build(config: dict[str, object], context: AdapterContext) -> SourceAdapter:
        return adapter(context.client, config_model.model_validate(config))

    return build


def _telegram(config: dict[str, object], context: AdapterContext) -> SourceAdapter:
    return TelegramAdapter(
        TelegramConfig.model_validate(config), forbidden_root=context.forbidden_root
    )


_BUILDERS: dict[str, Builder] = {
    "greenhouse": _http(BoardsConfig, GreenhouseAdapter),
    "lever": _http(CompaniesConfig, LeverAdapter),
    "ashby": _http(BoardsConfig, AshbyAdapter),
    "remotive": _http(RemotiveConfig, RemotiveAdapter),
    "remoteok": _http(RemoteOkConfig, RemoteOkAdapter),
    "arbeitnow": _http(ArbeitnowConfig, ArbeitnowAdapter),
    "hn_whoishiring": _http(HnConfig, HnHiringAdapter),
    "telegram": _telegram,
}


def source_kinds() -> list[str]:
    return sorted(_BUILDERS)


def build_adapter(kind: str, config: dict[str, object], context: AdapterContext) -> SourceAdapter:
    """Build an adapter from a stored SourceConfig row (kind, config)."""
    builder = _BUILDERS.get(kind)
    if builder is None:
        raise ValueError(f"unknown source kind {kind!r}; known kinds: {', '.join(source_kinds())}")
    return builder(config, context)
