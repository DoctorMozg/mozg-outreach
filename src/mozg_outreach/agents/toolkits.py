"""Toolkits: in-process tool sets an agent prompt opts into with `toolkits:` in its frontmatter."""

from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Protocol

from claude_agent_sdk.types import McpSdkServerConfig


class ToolkitUnavailable(RuntimeError):
    """The toolkit can't start right now; the agent runs without it."""


@dataclass(frozen=True)
class ToolkitSession:
    """One agent run's tools: an MCP server bound to that run's own resources."""

    server: McpSdkServerConfig
    tool_names: tuple[str, ...]  # full mcp__<toolkit>__<tool> names, for allowed_tools
    env: Mapping[str, str] = field(default_factory=dict)


class Toolkit(Protocol):
    name: str

    def session(self) -> AbstractAsyncContextManager[ToolkitSession]: ...


def mcp_tool_name(toolkit: str, tool: str) -> str:
    return f"mcp__{toolkit}__{tool}"
