from datetime import UTC, datetime

from mozg_outreach.pipelines.seeker.feeds import FeedRequest, JobFeeds, to_listing
from mozg_outreach.pipelines.seeker.models import SkillRules
from mozg_outreach.sources.models import FetchResult, SourceItem, SourceQuery

from .conftest import LONG_POSTING, TODAY

NOW = datetime(2026, 11, 14, 9, tzinfo=UTC)
PYTHON_OR_CPP = SkillRules(
    must_have_skills=[],
    must_have_or_groups=[["Python", "C++"]],
    nice_to_have_skills=[],
    synonym_clusters={},
)


def item(**fields: object) -> SourceItem:
    values: dict[str, object] = {
        "source": "greenhouse",
        "source_item_id": "acme/1",
        "url": "https://boards.greenhouse.io/acme/jobs/1",
        "kind": "job",
        "title": "Backend Engineer",
        "raw_text": "Python and PostgreSQL. Remote within the EU.",
        "raw_payload": {},
        "published_at": NOW,
        "fetched_at": NOW,
        "org_name": "acme",
        "location": "Remote - Europe",
    }
    return SourceItem.model_validate(values | fields)


class FakeAdapter:
    def __init__(self, name: str, result: FetchResult) -> None:
        self.name = name
        self._result = result
        self.calls: list[tuple[SourceQuery, str | None]] = []

    async def fetch(self, query: SourceQuery, cursor: str | None) -> FetchResult:
        self.calls.append((query, cursor))
        return self._result


def test_hn_title_splits_into_company_and_role() -> None:
    listing = to_listing(
        item(source="hn_whoishiring", org_name=None, title="Acme | Staff Engineer | Remote (EU)")
    )
    assert listing is not None
    assert (listing.company, listing.title) == ("Acme", "Staff Engineer")
    assert listing.remote_status_raw == "Remote"


def test_long_description_is_kept_whole() -> None:
    listing = to_listing(item(raw_text=LONG_POSTING))

    assert listing is not None
    assert listing.summary_snippet == LONG_POSTING


def test_item_without_employer_or_link_is_dropped() -> None:
    assert to_listing(item(org_name=None)) is None
    assert to_listing(item(url=None)) is None


async def test_collect_keeps_recent_jobs_and_cursors() -> None:
    fresh = FetchResult(
        items=[
            item(),
            item(source_item_id="acme/2", published_at=datetime(2026, 9, 1, tzinfo=UTC)),
            item(source_item_id="acme/3", kind="signal"),
        ],
        cursor="2026-11-14T09:00:00+00:00",
        errors=["GET https://boards.greenhouse.io/x 404"],
    )
    adapter = FakeAdapter("greenhouse", fresh)
    outcome = await JobFeeds([adapter]).collect(
        FeedRequest(
            skills=PYTHON_OR_CPP,
            recency_days=7,
            today=TODAY,
            cursors={"greenhouse": "2026-11-01T00:00:00+00:00"},
        )
    )
    assert [i.listing.url for i in outcome.listings] == ["https://boards.greenhouse.io/acme/jobs/1"]
    assert outcome.cursors == {"greenhouse": "2026-11-14T09:00:00+00:00"}
    assert outcome.concerns == ["greenhouse: GET https://boards.greenhouse.io/x 404"]
    assert adapter.calls[0][1] == "2026-11-01T00:00:00+00:00"


async def test_feeds_keep_jobs_naming_a_must_have_skill_whatever_their_title() -> None:
    result = FetchResult(
        items=[
            item(source_item_id="1", title="Senior Software Engineer", raw_text="C++17, CUDA"),
            item(source_item_id="2", title="Tech Lead Salesforce", raw_text="Apex and Flows"),
            item(source_item_id="3", title="ML Engineer", raw_text="PyTorch and python tooling"),
        ]
    )
    adapter = FakeAdapter("remoteok", result)

    outcome = await JobFeeds([adapter]).collect(
        FeedRequest(skills=PYTHON_OR_CPP, recency_days=7, today=TODAY, cursors={})
    )

    assert [i.listing.title for i in outcome.listings] == [
        "Senior Software Engineer",
        "ML Engineer",
    ]
    assert adapter.calls[0][0].keywords == [], "feeds come back whole; skills filter them here"
