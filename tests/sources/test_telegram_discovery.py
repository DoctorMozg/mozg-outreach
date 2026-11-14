import pytest

from mozg_outreach.sources import telegram_discovery
from mozg_outreach.sources.telegram import TelegramConfig
from mozg_outreach.sources.telegram_discovery import (
    ChannelDiscovery,
    DiscoveryRequest,
    job_share,
)
from tests.sources.conftest import CHAT, JOB, NOW, SearchSession, channel, posts


@pytest.fixture(autouse=True)
def no_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telegram_discovery, "PAUSE_SECONDS", 0)


def request(*terms: str, known: set[str] | None = None, limit: int = 10) -> DiscoveryRequest:
    return DiscoveryRequest(terms=list(terms), known=known or set(), limit=limit, now=NOW)


class TestFind:
    async def test_keeps_active_job_channels_and_drops_the_rest(self) -> None:
        session = SearchSession(
            {
                "python jobs": [
                    channel("py_jobs", 5000),
                    channel("py_chat", 9000, broadcast=False),
                    channel("py_stale"),
                    channel("py_memes"),
                    channel("py_private"),
                ]
            },
            {
                "py_jobs": posts(JOB, JOB, CHAT),
                "py_chat": posts(JOB, JOB),
                "py_stale": posts(JOB, JOB, age_days=30),
                "py_memes": posts(CHAT, CHAT, CHAT, JOB),
            },
        )

        result = await ChannelDiscovery(session).find(request("python"))

        assert [c.username for c in result.channels] == ["py_jobs"]
        [found] = result.channels
        assert (found.job_share, found.query, found.subscribers) == (0.67, "python jobs", 5000)
        assert any("py_private" in c for c in result.concerns), "unreadable channel is reported"

    async def test_known_channels_are_skipped_and_duplicates_vetted_once(self) -> None:
        session = SearchSession(
            {
                "python jobs": [channel("py_jobs"), channel("Old_Jobs")],
                "go jobs": [channel("py_jobs"), channel("go_jobs")],
            },
            {"py_jobs": posts(JOB), "go_jobs": posts(JOB), "Old_Jobs": posts(JOB)},
        )

        result = await ChannelDiscovery(session).find(request("python", "go", known={"old_jobs"}))

        assert sorted(c.username for c in result.channels) == ["go_jobs", "py_jobs"]

    async def test_ranked_by_job_share_then_subscribers_and_capped(self) -> None:
        session = SearchSession(
            {"python jobs": [channel("small", 100), channel("big", 9000), channel("mixed", 50000)]},
            {"small": posts(JOB), "big": posts(JOB), "mixed": posts(JOB, CHAT)},
        )

        result = await ChannelDiscovery(session).find(request("python", limit=2))

        assert [c.username for c in result.channels] == ["big", "small"]

    async def test_failed_search_is_a_concern_and_other_terms_continue(self) -> None:
        session = SearchSession({"go jobs": [channel("go_jobs")]}, {"go_jobs": posts(JOB)})

        result = await ChannelDiscovery(session).find(request("broken", "go"))

        assert [c.username for c in result.channels] == ["go_jobs"]
        assert result.concerns == ["Telegram search failed: FloodWaitError searching 'broken jobs'"]

    async def test_query_count_is_capped(self) -> None:
        session = SearchSession({}, {})
        terms = [f"skill{i}" for i in range(12)]

        await ChannelDiscovery(session).find(request(*terms))

        assert len(session.queries) == telegram_discovery.MAX_QUERIES


def test_job_share_ignores_empty_posts() -> None:
    assert job_share(posts(JOB, "  ", CHAT)) == 0.5
    assert job_share([]) == 0.0


class TestConfig:
    def test_needs_channels_or_discovery(self) -> None:
        with pytest.raises(ValueError, match="discover"):
            TelegramConfig.model_validate({"channels": []})
        assert TelegramConfig.model_validate({"discover": True}).channels == []
