from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from mozg_outreach.agents import AgentRunError, PromptLibrary
from mozg_outreach.pipelines.seeker import telegram_posts
from mozg_outreach.pipelines.seeker.models import TelegramContact
from mozg_outreach.pipelines.seeker.telegram_posts import (
    ParsedPost,
    ParserResult,
    TelegramPosts,
    TelegramRequest,
    telegram_contact,
    to_scouted,
)
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.sources.models import FetchResult, SourceItem, SourceQuery
from tests.pipelines.conftest import REPO_PROMPTS, RecordedRunner, Scripted

POSTED = datetime(2026, 11, 13, 9, tzinfo=UTC)
VACANCY = "https://career.habr.com/vacancies/1000"
POST = "Senior Python-разработчик в Альфа-Банк, удалённо, от 400к. Отклик: @hr_anna"


def post(n: int, text: str = POST, links: list[str] | None = None, **fields: object) -> SourceItem:
    values: dict[str, object] = {
        "source": "telegram",
        "source_item_id": f"py_jobs/{n}",
        "url": f"https://t.me/py_jobs/{n}",
        "kind": "job",
        "title": text.split("\n", 1)[0],
        "raw_text": text,
        "raw_payload": {"channel": "py_jobs", "message_id": n, "links": links or [VACANCY]},
        "published_at": POSTED,
        "fetched_at": POSTED,
    }
    return SourceItem.model_validate(values | fields)


def parsed(n: int, **fields: object) -> ParsedPost:
    values: dict[str, object] = {
        "id": f"py_jobs/{n}",
        "is_job": True,
        "title": "Senior Python-разработчик",
        "company": "Альфа-Банк",
        "location": None,
        "remote": "remote",
        "salary": "от 400к",
        "apply_url": VACANCY,
        "contact_handle": "@hr_anna",
        "contact_name": None,
        "reason": "Concrete role.",
    }
    return ParsedPost.model_validate(values | fields)


class FakeChannel:
    name = "telegram"

    def __init__(self, items: list[SourceItem]) -> None:
        self.items = items
        self.queries: list[SourceQuery] = []

    async def fetch(self, query: SourceQuery, cursor: str | None) -> FetchResult:
        self.queries.append(query)
        return FetchResult(items=self.items, cursor='{"py_jobs": 99}')


def request() -> TelegramRequest:
    return TelegramRequest(skills=["Python"], oldest=date(2026, 11, 1), cursor=None)


def posts_for(tmp_path: Path, script: list[Scripted]) -> tuple[TelegramPosts, RecordedRunner]:
    runner = RecordedRunner({"telegram-post-parser": script})
    return TelegramPosts(PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path)), runner


class TestToScouted:
    def test_job_post_keeps_its_vacancy_link_and_lists_every_link(self) -> None:
        concerns: list[str] = []
        listing = to_scouted(post(1, links=[VACANCY, "https://t.me/hr_anna"]), parsed(1), concerns)

        assert listing is not None
        assert (listing.company, listing.apply_url, listing.remote_status_raw) == (
            "Альфа-Банк",
            VACANCY,
            "Remote",
        )
        assert listing.url == "https://t.me/py_jobs/1"
        assert listing.summary_snippet.endswith(f"Links: {VACANCY} https://t.me/hr_anna")
        assert concerns == []

    def test_company_missing_from_the_post_is_dropped(self) -> None:
        concerns: list[str] = []
        listing = to_scouted(post(1), parsed(1, company="Alfa Bank"), concerns)

        assert listing is not None
        assert listing.company == ""
        assert concerns == ["py_jobs/1: dropped company 'Alfa Bank', not in the post."]

    def test_apply_link_not_among_the_post_links_is_dropped(self) -> None:
        concerns: list[str] = []
        listing = to_scouted(post(1), parsed(1, apply_url="https://alfabank.ru/jobs"), concerns)

        assert listing is not None
        assert listing.apply_url is None
        assert len(concerns) == 1

    @pytest.mark.parametrize(
        ("item", "result"),
        [
            (post(1), parsed(1, is_job=False, title=None)),
            (post(1), parsed(1, title="  ")),
            (post(1, url=None), parsed(1)),
        ],
        ids=["not a job", "no title", "private channel without a link"],
    )
    def test_unusable_posts_give_no_listing(self, item: SourceItem, result: ParsedPost) -> None:
        assert to_scouted(item, result, []) is None


class TestContact:
    def test_handle_in_the_post_is_kept_with_its_line(self) -> None:
        text = "Python developer at Acme\nWrite to Anna: @HR_Anna"
        result = parsed(1, contact_handle="@hr_anna", contact_name="Anna")

        contact = telegram_contact(post(1, text=text), result, [])

        quote = "Write to Anna: @HR_Anna"
        assert contact == TelegramContact(handle="hr_anna", name="Anna", quote=quote)

    def test_handle_only_in_a_link_quotes_the_link(self) -> None:
        item = post(1, text="Python developer, apply below", links=["https://t.me/hr_anna"])

        contact = telegram_contact(item, parsed(1, contact_name="Anna"), [])

        assert contact == TelegramContact(handle="hr_anna", name=None, quote="https://t.me/hr_anna")

    @pytest.mark.parametrize(
        ("text", "handle"),
        [
            ("Write to @hr_annabel", "@hr_anna"),
            ("Subscribe to @py_jobs", "@py_jobs"),
            ("Apply via @jobs_apply_bot", "@jobs_apply_bot"),
        ],
        ids=["not in the post", "the channel itself", "a bot"],
    )
    def test_handles_that_are_no_person_in_the_post_are_dropped(
        self, text: str, handle: str
    ) -> None:
        concerns: list[str] = []

        contact = telegram_contact(post(1, text=text), parsed(1, contact_handle=handle), concerns)

        assert contact is None
        assert len(concerns) == 1


class TestCollect:
    async def test_batches_recent_posts_and_moves_the_cursor(self, tmp_path: Path) -> None:
        stale = post(99, published_at=datetime(2026, 10, 1, tzinfo=UTC))
        items = [post(n) for n in range(1, 26)] + [stale]
        script: list[Scripted] = [
            ParserResult(posts=[parsed(n) for n in range(1, 21)], concerns=[]),
            ParserResult(posts=[parsed(n) for n in range(21, 26)], concerns=[]),
        ]
        posts, runner = posts_for(tmp_path, script)
        channel = FakeChannel(items)

        outcome = await posts.collect(channel, request())

        assert [len(p.model_dump()["posts"]) for p in runner.payloads["telegram-post-parser"]] == [
            20,
            5,
        ]
        assert len(outcome.listings) == 25
        assert outcome.cursors == {"telegram": '{"py_jobs": 99}'}
        assert channel.queries[0].keywords == ["Python"]

    async def test_failed_batch_keeps_the_cursor_and_unknown_ids_are_reported(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(telegram_posts, "BATCH", 2)
        script: list[Scripted] = [
            ParserResult(posts=[parsed(1), parsed(7)], concerns=[]),
            AgentRunError("telegram-post-parser", "error_during_execution", "rate limited"),
        ]
        posts, _ = posts_for(tmp_path, script)

        outcome = await posts.collect(FakeChannel([post(1), post(2), post(3)]), request())

        assert [s.listing.url for s in outcome.listings] == ["https://t.me/py_jobs/1"]
        assert outcome.cursors == {"telegram": None}, "the next run reads the failed posts again"
        assert any("unknown post id 'py_jobs/7'" in c for c in outcome.concerns)
        assert any(c.startswith(telegram_posts.UNPARSED) for c in outcome.concerns)
