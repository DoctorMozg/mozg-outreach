from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mozg_outreach.agents import PromptLibrary
from mozg_outreach.domain import BriefFact
from mozg_outreach.letters.models import LetterBrief, LetterDraft
from mozg_outreach.naturalize.models import NaturalizeResult
from mozg_outreach.naturalize.punctuation import PunctuationChecker
from mozg_outreach.naturalize.validation import body_word_count
from mozg_outreach.pipelines.biz.discovery import dedup_companies
from mozg_outreach.pipelines.biz.enrichment_models import (
    CompanyDossier,
    ContactsResult,
    GrowthResult,
    NewsResult,
    ReputationResult,
    TechResult,
)
from mozg_outreach.pipelines.biz.models import BizBrief, ScoutResult, Strategy
from mozg_outreach.pipelines.biz.outreach import DOSSIER, BizOutreach
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.storage.models import Entity, Item, Project, Run
from mozg_outreach.storage.repositories import DraftRepository, ItemRepository
from tests.pipelines.biz.conftest import BODY, HOOK, STYLE, load_fixture
from tests.pipelines.conftest import REPO_PROMPTS, RecordedRunner

LETTER = f"Hello Jane,\n\n{BODY}\n\nBest regards,\nStan"


@pytest.fixture
def item(session: Session, project: Project, run: Run) -> Item:
    katana = dedup_companies(load_fixture("scout_results", list[ScoutResult])[0].companies)[0]
    row = ItemRepository(session).upsert(project.id, run.id, katana)
    row.selected = True
    return row


@pytest.fixture
def outreach(session: Session, tmp_path: Path) -> tuple[BizOutreach, RecordedRunner]:
    runner = RecordedRunner(
        {
            "outreach-scanner": [load_fixture("enrichment/reputation", ReputationResult)],
            "outreach-contact-finder": [load_fixture("enrichment/contacts", ContactsResult)],
            "outreach-news-finder": [load_fixture("enrichment/news", NewsResult)],
            "outreach-growth-analyst": [load_fixture("enrichment/growth", GrowthResult)],
            "outreach-tech-analyst": [load_fixture("enrichment/tech", TechResult)],
            "outreach-personality-profiler": [STYLE],
            "expert-copywriter": [
                LetterDraft(
                    subject="Katana's platform load after the round",
                    body=BODY,
                    hook_used=HOOK,
                    angle_used="cost",
                    concerns=[],
                )
            ],
            "expert-naturalizer": [
                NaturalizeResult(text=LETTER, changes=[], specificity_needed=[], concerns=[])
            ],
        }
    )
    steps = PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path)
    return BizOutreach(session, steps), runner


async def test_selected_item_becomes_enriched_scored_and_drafted(
    session: Session,
    project: Project,
    item: Item,
    strategy: Strategy,
    outreach: tuple[BizOutreach, RecordedRunner],
) -> None:
    service, runner = outreach
    brief = load_fixture("brief", BizBrief)
    brief.facts = [BriefFact(label="Clients", value="Siemens", source_url="https://us.example")]
    project.brief = brief.model_dump(mode="json")

    dossier = await service.enrich(item, strategy)
    batch = await service.write_letters(item, strategy)

    assert CompanyDossier.model_validate(item.details[DOSSIER]) == dossier
    assert item.score_total is not None
    assert {e.name for e in session.scalars(select(Entity))} >= {item.title, "Jane Example"}
    assert batch.skipped == []
    [draft] = DraftRepository(session).for_item(item.id)
    assert (draft.channel, draft.recipient, draft.version) == ("email", "Jane Example", 1)
    assert draft.subject is not None
    assert 0 < len(draft.subject) <= 55
    lines = draft.body.splitlines()
    assert lines[0] == "Hello Jane,"
    assert lines[-2:] == ["Best regards,", "Stan"]
    assert 120 <= body_word_count(draft.body) <= 180
    assert PunctuationChecker().check(f"{draft.subject}\n{draft.body}") == []
    assert draft.review["problems"] == []
    assert draft.review["address"] == "hello@katanamrp.com"
    [letter_brief] = runner.payloads["expert-copywriter"]
    assert isinstance(letter_brief, LetterBrief)
    assert letter_brief.sender_background == "- Clients: Siemens"


class TestPreconditions:
    async def test_letters_need_enrichment_first(
        self, item: Item, strategy: Strategy, outreach: tuple[BizOutreach, RecordedRunner]
    ) -> None:
        with pytest.raises(ValueError, match="not enriched"):
            await outreach[0].write_letters(item, strategy)

    async def test_letters_need_a_sender(
        self,
        item: Item,
        project: Project,
        strategy: Strategy,
        outreach: tuple[BizOutreach, RecordedRunner],
    ) -> None:
        service, runner = outreach
        await service.enrich(item, strategy)
        project.sender_name = None
        with pytest.raises(ValueError, match="no sender name"):
            await service.write_letters(item, strategy)
        assert "expert-copywriter" not in runner.payloads
