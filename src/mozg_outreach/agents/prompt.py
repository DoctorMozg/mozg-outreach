import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast, get_args

from claude_agent_sdk.types import EffortLevel

# Agents return structured output instead of writing files, and fan-out happens in Python,
# so file, shell and subagent tools from the old plugin frontmatter are dropped.
ALLOWED_TOOLS = frozenset({"WebSearch", "WebFetch"})
_TOOL_TOKEN = re.compile(r"[A-Za-z]+(?:\([^)]*\))?")
_EFFORT_LEVELS: frozenset[str] = frozenset(get_args(EffortLevel))


class PromptError(ValueError):
    """A prompt file is missing or its frontmatter is malformed."""


@dataclass(frozen=True)
class AgentPrompt:
    group: str
    name: str
    model: str
    tools: tuple[str, ...]
    max_turns: int | None
    effort: EffortLevel | None
    body: str
    toolkits: tuple[str, ...] = ()


class PromptLibrary:
    """Loads agent prompts from `<root>/<group>/<name>.md` files with frontmatter."""

    def __init__(self, root: Path) -> None:
        if not root.is_dir():
            raise PromptError(f"prompt root is not a directory: {root}")
        self._root = root
        self._cache: dict[tuple[str, str], AgentPrompt] = {}

    def get(self, group: str, name: str) -> AgentPrompt:
        key = (group, name)
        if key not in self._cache:
            self._cache[key] = self.parse(self._root / group / f"{name}.md")
        return self._cache[key]

    def all(self) -> list[AgentPrompt]:
        return [self.get(p.parent.name, p.stem) for p in sorted(self._root.glob("*/*.md"))]

    @staticmethod
    def parse(path: Path) -> AgentPrompt:
        if not path.is_file():
            raise PromptError(f"prompt file not found: {path}")
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines or lines[0] != "---" or "---" not in lines[1:]:
            raise PromptError(f"{path}: missing frontmatter block")
        end = lines.index("---", 1)
        fields: dict[str, str] = {}
        for line in lines[1:end]:
            key, sep, value = line.partition(":")
            if sep and not key.startswith(" "):
                fields[key.strip()] = value.strip()

        if fields.get("name") != path.stem:
            raise PromptError(f"{path}: frontmatter name {fields.get('name')!r} != file name")
        model = fields.get("model")
        if not model:
            raise PromptError(f"{path}: frontmatter has no model")
        effort = fields.get("effort")
        if effort is not None and effort not in _EFFORT_LEVELS:
            raise PromptError(f"{path}: unknown effort {effort!r}")
        max_turns = fields.get("maxTurns")
        declared = _TOOL_TOKEN.findall(fields.get("tools", ""))
        toolkits = tuple(t.strip() for t in fields.get("toolkits", "").split(",") if t.strip())

        return AgentPrompt(
            group=path.parent.name,
            name=path.stem,
            model=model,
            tools=tuple(t for t in declared if t in ALLOWED_TOOLS),
            max_turns=int(max_turns) if max_turns else None,
            effort=cast("EffortLevel | None", effort),
            body="\n".join(lines[end + 1 :]).strip(),
            toolkits=toolkits,
        )
