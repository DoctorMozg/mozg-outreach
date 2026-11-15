from pathlib import Path

import pytest

from mozg_outreach.agents import AgentPrompt, PromptError, PromptLibrary

REPO_PROMPTS = Path(__file__).resolve().parents[2] / "prompts"


class TestParse:
    def test_keeps_only_web_tools_and_reads_limits(self, sample_prompt: AgentPrompt) -> None:
        assert sample_prompt.tools == ("WebFetch", "WebSearch")
        assert sample_prompt.model == "sonnet"
        assert sample_prompt.effort == "medium"
        assert sample_prompt.max_turns == 12
        assert sample_prompt.body == "You are a scout."

    def test_name_must_match_file(self, prompt_root: Path) -> None:
        path = prompt_root / "biz" / "renamed.md"
        path.write_text((prompt_root / "biz" / "sample-scout.md").read_text(), encoding="utf-8")
        with pytest.raises(PromptError, match="!= file name"):
            PromptLibrary(prompt_root).get("biz", "renamed")

    def test_missing_frontmatter(self, prompt_root: Path) -> None:
        (prompt_root / "biz" / "bare.md").write_text("just text\n", encoding="utf-8")
        with pytest.raises(PromptError, match="missing frontmatter"):
            PromptLibrary(prompt_root).get("biz", "bare")

    def test_unknown_effort(self, prompt_root: Path) -> None:
        text = (prompt_root / "biz" / "sample-scout.md").read_text().replace("medium", "turbo")
        (prompt_root / "biz" / "sample-scout.md").write_text(text, encoding="utf-8")
        with pytest.raises(PromptError, match="unknown effort"):
            PromptLibrary(prompt_root).get("biz", "sample-scout")

    def test_missing_file(self, prompt_root: Path) -> None:
        with pytest.raises(PromptError, match="not found"):
            PromptLibrary(prompt_root).get("biz", "nope")


class TestRepoPrompts:
    @pytest.fixture(scope="class")
    def prompts(self) -> list[AgentPrompt]:
        return PromptLibrary(REPO_PROMPTS).all()

    def test_all_migrated_prompts_load(self, prompts: list[AgentPrompt]) -> None:
        counts: dict[str, int] = {}
        for p in prompts:
            counts[p.group] = counts.get(p.group, 0) + 1
        assert counts == {"biz": 13, "creative": 3, "seeker": 8, "shared": 5}

    def test_models_are_known_aliases(self, prompts: list[AgentPrompt]) -> None:
        assert {p.model for p in prompts} <= {"sonnet", "opus", "haiku"}
