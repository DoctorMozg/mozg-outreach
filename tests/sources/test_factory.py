import httpx
import pytest
from pydantic import ValidationError

from mozg_outreach.sources.ats import GreenhouseAdapter
from mozg_outreach.sources.base import SourceAdapter
from mozg_outreach.sources.factory import AdapterContext, build_adapter, source_kinds
from mozg_outreach.sources.telegram import TelegramAdapter

CONFIGS: dict[str, dict[str, object]] = {
    "greenhouse": {"boards": ["acme"]},
    "lever": {"companies": ["acme"]},
    "ashby": {"boards": ["acme"]},
    "remotive": {},
    "remoteok": {},
    "arbeitnow": {"max_pages": 2},
    "hn_whoishiring": {},
    "himalayas": {"max_pages": 1},
    "weworkremotely": {"feeds": ["remote-jobs"]},
    "workingnomads": {},
    "telegram": {"channels": ["jobs_chan"]},
}


@pytest.fixture
def context() -> AdapterContext:
    return AdapterContext(client=httpx.AsyncClient())


def test_every_kind_has_a_sample_config() -> None:
    assert set(CONFIGS) == set(source_kinds())


@pytest.mark.parametrize("kind", sorted(CONFIGS))
def test_builds_each_kind_with_its_name(kind: str, context: AdapterContext) -> None:
    adapter = build_adapter(kind, CONFIGS[kind], context)

    assert isinstance(adapter, SourceAdapter)
    assert adapter.name == kind


def test_dispatch_returns_concrete_types(context: AdapterContext) -> None:
    assert isinstance(
        build_adapter("greenhouse", CONFIGS["greenhouse"], context), GreenhouseAdapter
    )
    assert isinstance(build_adapter("telegram", CONFIGS["telegram"], context), TelegramAdapter)


def test_unknown_kind_names_it(context: AdapterContext) -> None:
    with pytest.raises(ValueError, match="'linkedin'"):
        build_adapter("linkedin", {}, context)


@pytest.mark.parametrize(
    ("kind", "config"),
    [
        ("greenhouse", {}),
        ("greenhouse", {"boards": []}),
        ("lever", {"companies": ["a"], "typo": 1}),
        ("arbeitnow", {"max_pages": 0}),
        ("telegram", {"channels": "not-a-list"}),
    ],
)
def test_bad_config_is_rejected(
    kind: str, config: dict[str, object], context: AdapterContext
) -> None:
    with pytest.raises(ValidationError):
        build_adapter(kind, config, context)
