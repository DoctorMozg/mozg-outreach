import time
from datetime import UTC, datetime

from mozg_outreach.sources.base import RateLimiter, matches_keywords, select_items
from mozg_outreach.sources.models import SourceItem, SourceQuery
from mozg_outreach.sources.text import html_to_text, unescape_html_to_text

NOW = datetime(2026, 11, 14, tzinfo=UTC)


def make_item(
    item_id: str = "1",
    *,
    title: str | None = "Python Developer",
    text: str = "Build services",
    published: datetime | None = None,
) -> SourceItem:
    return SourceItem(
        source="test",
        source_item_id=item_id,
        kind="job",
        title=title,
        raw_text=text,
        raw_payload={},
        published_at=published,
        fetched_at=NOW,
    )


class TestSourceItem:
    def test_hash_ignores_whitespace_differences(self) -> None:
        a = make_item(text="Build   services\n in Rust")
        b = make_item(text="Build services in Rust")
        assert a.content_hash == b.content_hash
        assert a.content_hash != make_item(text="Build services in Go").content_hash

    def test_naive_timestamps_become_utc(self) -> None:
        item = make_item(published=datetime(2026, 11, 11, 20, 17))
        assert item.published_at == datetime(2026, 11, 11, 20, 17, tzinfo=UTC)

    def test_dedup_key_is_source_and_id(self) -> None:
        assert make_item("42").dedup_key == ("test", "42")


class TestFilters:
    def test_empty_keywords_keep_everything(self) -> None:
        assert matches_keywords(make_item(), [])

    def test_keyword_match_is_case_insensitive_on_title_and_text(self) -> None:
        item = make_item(title="Backend", text="We use PostgreSQL")
        assert matches_keywords(item, ["go", "postgresql"])
        assert not matches_keywords(item, ["rust"])

    def test_since_drops_older_but_keeps_undated(self) -> None:
        old = make_item("old", published=datetime(2026, 1, 1, tzinfo=UTC))
        new = make_item("new", published=datetime(2026, 11, 1, tzinfo=UTC))
        undated = make_item("undated")
        query = SourceQuery(since=datetime(2026, 6, 1, tzinfo=UTC))
        kept = select_items([old, new, undated], query)
        assert {i.source_item_id for i in kept} == {"new", "undated"}

    def test_limit_keeps_the_newest(self) -> None:
        items = [
            make_item(str(day), published=datetime(2026, 11, day, tzinfo=UTC)) for day in (1, 5, 3)
        ]
        kept = select_items(items, SourceQuery(limit=2))
        assert [i.source_item_id for i in kept] == ["5", "3"]


class TestRateLimiter:
    async def test_second_call_waits_for_the_interval(self) -> None:
        limiter = RateLimiter(0.1)
        await limiter.wait()
        started = time.monotonic()
        await limiter.wait()
        assert time.monotonic() - started >= 0.08

    async def test_zero_interval_does_not_wait(self) -> None:
        limiter = RateLimiter(0)
        started = time.monotonic()
        for _ in range(3):
            await limiter.wait()
        assert time.monotonic() - started < 0.05


class TestHtmlToText:
    def test_blocks_become_lines_and_lists_get_bullets(self) -> None:
        text = html_to_text(
            "<h2>Role</h2><p>Build <b>APIs</b></p><ul><li>Python</li><li>SQL</li></ul>"
        )
        assert text == "Role\nBuild APIs\n- Python\n- SQL"

    def test_scripts_are_dropped(self) -> None:
        assert html_to_text("<p>Hi</p><script>alert(1)</script>") == "Hi"

    def test_escaped_markup_is_unescaped_first(self) -> None:
        assert unescape_html_to_text("&lt;p&gt;A &amp;amp; B&lt;/p&gt;") == "A & B"


def test_blank_keywords_are_ignored() -> None:
    assert not matches_keywords(make_item(), [" "])
