from pathlib import Path

import pytest
from pydantic import BaseModel

from mozg_outreach.agents import AgentRunError, PromptLibrary
from mozg_outreach.domain import FieldValue, ItemCandidate, ItemKind
from mozg_outreach.pipelines.biz.enrichment import BizEnrichment, brief_from_candidate
from mozg_outreach.pipelines.biz.enrichment_models import (
    CompanyBrief,
    ContactsResult,
    GrowthResult,
    NewsResult,
    ReputationResult,
    TechResult,
)
from mozg_outreach.pipelines.biz.models import Strategy
from mozg_outreach.pipelines.steps import PipelineSteps
from tests.pipelines.biz.conftest import load_fixture
from tests.pipelines.conftest import REPO_PROMPTS, RecordedRunner, Scripted


def load[TModel: BaseModel](name: str, model: type[TModel]) -> TModel:
    return load_fixture(f"enrichment/{name}", model)


@pytest.fixture(scope="module")
def company(strategy: Strategy) -> CompanyBrief:
    return CompanyBrief(
        name="Katana",
        domain="katanamrp.com",
        location="Tallinn, Estonia",
        sector="B2B SaaS",
        target_profile=strategy.target_profile,
        outreach_angles=strategy.outreach_angles,
    )


@pytest.fixture
def script() -> dict[str, list[Scripted]]:
    return {
        "outreach-scanner": [load("reputation", ReputationResult)],
        "outreach-contact-finder": [load("contacts", ContactsResult)],
        "outreach-news-finder": [load("news", NewsResult)],
        "outreach-growth-analyst": [load("growth", GrowthResult)],
        "outreach-tech-analyst": [load("tech", TechResult)],
    }


def enrichment(runner: RecordedRunner, tmp_path: Path) -> BizEnrichment:
    return BizEnrichment(PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path))


async def test_full_dossier(
    tmp_path: Path, company: CompanyBrief, script: dict[str, list[Scripted]]
) -> None:
    runner = RecordedRunner(script)
    steps = PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path)
    e = BizEnrichment(steps)

    dossier = await e.enrich(company)

    assert dossier.concerns == []
    assert dossier.reputation is not None
    assert dossier.reputation.reviews.glassdoor.sentiment == "positive"
    assert dossier.news is not None
    assert dossier.news.items[0].url.startswith("https://estonia.ee/")
    assert dossier.growth is not None
    assert dossier.growth.trajectory == "rapid_growth"
    assert dossier.tech is not None
    assert dossier.tech.maturity == "mid"
    assert steps.cost_usd == pytest.approx(0.5)
    assert all(payloads == [company] for payloads in runner.payloads.values())


async def test_failed_agent_becomes_a_concern(
    tmp_path: Path, company: CompanyBrief, script: dict[str, list[Scripted]]
) -> None:
    script["outreach-news-finder"] = [
        AgentRunError("outreach-news-finder", "error_max_turns", "ran out of turns")
    ]
    dossier = await enrichment(RecordedRunner(script), tmp_path).enrich(company)

    assert dossier.news is None
    assert dossier.contacts is not None
    assert dossier.tech is not None
    assert len(dossier.concerns) == 1
    assert dossier.concerns[0].startswith("News for Katana failed")


async def test_unexpected_error_propagates(
    tmp_path: Path, company: CompanyBrief, script: dict[str, list[Scripted]]
) -> None:
    script["outreach-tech-analyst"] = [RuntimeError("boom")]
    with pytest.raises(RuntimeError, match="boom"):
        await enrichment(RecordedRunner(script), tmp_path).enrich(company)


async def test_enrich_many_keeps_input_order(
    tmp_path: Path, company: CompanyBrief, script: dict[str, list[Scripted]]
) -> None:
    other = company.model_copy(update={"name": "Blackwall"})
    doubled = {agent: results * 2 for agent, results in script.items()}
    dossiers = await enrichment(RecordedRunner(doubled), tmp_path).enrich_many([company, other])
    assert len(dossiers) == 2
    assert all(d.concerns == [] for d in dossiers)


def test_contacts_keep_pattern_guess_kind() -> None:
    contacts = load("contacts", ContactsResult)
    restored = ContactsResult.model_validate_json(contacts.model_dump_json())
    assert [(e.address, e.kind) for e in restored.emails] == [
        ("hello@katanamrp.com", "published"),
        ("partnerships@katanamrp.com", "pattern_guess"),
    ]
    assert all(e.source_url for e in restored.emails)
    assert all(p.source_url for p in restored.key_people)


def test_brief_from_candidate(strategy: Strategy) -> None:
    candidate = ItemCandidate(
        kind=ItemKind.COMPANY,
        dedup_key="katanamrp.com",
        title="Katana",
        fields={
            "domain": FieldValue(value="katanamrp.com"),
            "location": FieldValue(value="Tallinn, Estonia"),
            "source_urls": FieldValue(value=["https://estonia.ee/x"]),
        },
    )
    brief = brief_from_candidate(candidate, strategy)
    assert (brief.name, brief.domain, brief.location) == (
        "Katana",
        "katanamrp.com",
        "Tallinn, Estonia",
    )
    assert brief.sector is None
    assert brief.target_profile == strategy.target_profile
    assert brief.outreach_angles == strategy.outreach_angles
