from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mozg_outreach.agents import PromptLibrary
from mozg_outreach.domain import BriefFact, FieldValue
from mozg_outreach.letters.frame import WORDINGS
from mozg_outreach.letters.models import LetterBrief, Revision, RevisionTarget
from mozg_outreach.naturalize.punctuation import PunctuationChecker
from mozg_outreach.naturalize.validation import body_word_count
from mozg_outreach.pipelines.biz.approach_models import StoredApproach
from mozg_outreach.pipelines.biz.discovery import dedup_companies
from mozg_outreach.pipelines.biz.enrichment_models import (
    CompanyDossier,
)
from mozg_outreach.pipelines.biz.models import BizBrief, ScoutResult, Strategy
from mozg_outreach.pipelines.biz.outreach import APPROACH, DOSSIER, BizOutreach
from mozg_outreach.pipelines.biz.progress import ApproachProgress
from mozg_outreach.pipelines.steps import PipelineSteps
from mozg_outreach.storage.models import Entity, Item, Project, Run
from mozg_outreach.storage.repositories import DraftRepository, ItemRepository
from tests.pipelines.biz.conftest import load_fixture, outreach_script
from tests.pipelines.conftest import REPO_PROMPTS, RecordedRunner


@pytest.fixture
def item(session: Session, project: Project, run: Run) -> Item:
    katana = dedup_companies(load_fixture("scout_results", list[ScoutResult])[0].companies)[0]
    row = ItemRepository(session).upsert(project.id, run.id, katana)
    row.selected = True
    return row


@pytest.fixture
def outreach(session: Session, tmp_path: Path) -> tuple[BizOutreach, RecordedRunner]:
    runner = RecordedRunner(outreach_script())
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
    assert item.enriched
    assert item.enriched_at is not None
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
    assert WORDINGS["en"].opt_out not in draft.body, "Tallinn is outside the GCC"
    assert 60 <= body_word_count(draft.body) <= 100
    assert PunctuationChecker().check(f"{draft.subject}\n{draft.body}") == []
    assert draft.review["problems"] == []
    assert draft.review["address"] == "hello@katanamrp.com"
    [letter_brief] = runner.payloads["expert-copywriter"]
    assert isinstance(letter_brief, LetterBrief)
    assert letter_brief.sender_background == "- Clients: Siemens"


async def test_every_planned_step_gets_its_own_letter(
    session: Session,
    item: Item,
    strategy: Strategy,
    outreach: tuple[BizOutreach, RecordedRunner],
) -> None:
    service, runner = outreach

    await service.enrich(item, strategy)
    planned = await service.plan_approach(item, strategy)
    batch = await service.write_letters(item, strategy)

    assert StoredApproach.model_validate(item.details[APPROACH]) == planned
    assert batch.skipped == []
    touches = planned.plan.touches
    assert [t.day for t in touches] == [0, 4, 11]
    briefs = [b for b in runner.payloads["expert-copywriter"] if isinstance(b, LetterBrief)]
    opener, follow_up, breakup = briefs
    assert (opener.recipient.name, opener.generic_inbox) == ("Jane Example", True)
    assert [a.name for a in opener.angles] == ["Platform capacity gap"]
    assert [h.signal for h in opener.hooks] == [planned.hooks[0].hook.signal]
    assert [b.ask for b in briefs] == [t.ask for t in touches]
    contexts = [b.touch for b in briefs if b.touch]
    assert [(c.step, c.total, c.purpose, c.in_thread) for c in contexts] == [
        (1, 3, "open", False),
        (2, 3, "follow_up", True),
        (3, 3, "breakup", True),
    ]
    # Replies in the thread take the opener's subject, and each follow-up sees what came before.
    assert [b.subject_max_chars for b in briefs] == [55, None, None]
    assert [(b.word_budget.min, b.word_budget.max) for b in briefs] == [
        (60, 100),
        (40, 110),
        (30, 70),
    ]
    assert follow_up.touch
    assert [e.step for e in follow_up.touch.earlier] == [1]
    assert breakup.touch
    assert [e.step for e in breakup.touch.earlier] == [1, 2]
    assert breakup.touch.earlier[0].text == batch.letters[0].text
    drafts = DraftRepository(session).for_item(item.id)
    assert [(d.step, d.recipient, d.subject is None) for d in drafts] == [
        (1, "Jane Example", False),
        (2, "Jane Example", True),
        (3, "Jane Example", True),
    ]
    assert all(d.review["problems"] == [] for d in drafts)


async def test_rewriting_one_step_keeps_the_others_as_context(
    session: Session, item: Item, strategy: Strategy, tmp_path: Path
) -> None:
    script = outreach_script()
    # The rewrite reads the reader again and redrafts only the follow-up.
    for agent, index in (
        ("outreach-personality-profiler", 0),
        ("expert-copywriter", 1),
        ("expert-naturalizer", 1),
    ):
        script[agent].append(script[agent][index])
    runner = RecordedRunner(script)
    service = BizOutreach(session, PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path))
    await service.enrich(item, strategy)
    await service.plan_approach(item, strategy)
    await service.write_letters(item, strategy)
    step_two = DraftRepository(session).for_item(item.id)[1]
    target = RevisionTarget(
        recipient="Jane Example",
        channel="email",
        step=2,
        revision=Revision(previous_text=step_two.body, suggestion="Shorter."),
    )

    batch = await service.write_letters(item, strategy, target)

    assert [(letter.step, letter.recipient) for letter in batch.letters] == [(2, "Jane Example")]
    brief = runner.payloads["expert-copywriter"][-1]
    assert isinstance(brief, LetterBrief)
    assert brief.revision
    assert brief.revision.suggestion == "Shorter."
    assert brief.touch
    assert brief.touch.earlier[0].text == DraftRepository(session).for_item(item.id)[0].body
    versions = [(d.step, d.version) for d in DraftRepository(session).for_item(item.id)]
    assert versions == [(1, 1), (2, 1), (2, 2), (3, 1)]


async def test_letters_to_gcc_companies_offer_to_stop(
    session: Session, item: Item, strategy: Strategy, outreach: tuple[BizOutreach, RecordedRunner]
) -> None:
    dubai = FieldValue(value="Dubai, United Arab Emirates", source_url="https://katana.example")
    item.fields = {**item.fields, "location": dubai.model_dump(mode="json")}
    service, _ = outreach

    await service.enrich(item, strategy)
    await service.write_letters(item, strategy)

    [draft] = DraftRepository(session).for_item(item.id)
    assert draft.body.splitlines()[-4:] == [WORDINGS["en"].opt_out, "", "Best regards,", "Stan"]
    assert 60 <= body_word_count(str(draft.review["before_naturalize"])) <= 100


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


async def test_enrichment_stores_the_domain_it_found(
    item: Item, strategy: Strategy, outreach: tuple[BizOutreach, RecordedRunner]
) -> None:
    item.fields = {k: v for k, v in item.fields.items() if k != "domain"}
    service, _ = outreach

    await service.enrich(item, strategy)

    stored = FieldValue.model_validate(item.fields["domain"])
    assert stored.value == "katanamrp.com"
    assert stored.source_url == "https://katanamrp.com/about/"


async def test_rewriting_a_step_the_plan_dropped_is_skipped(
    item: Item, strategy: Strategy, outreach: tuple[BizOutreach, RecordedRunner]
) -> None:
    service, runner = outreach
    await service.enrich(item, strategy)
    await service.plan_approach(item, strategy)
    revision = Revision(previous_text="Hello Jane,\n\nOld.\n\nBest regards,\nStan", suggestion="x")
    target = RevisionTarget(recipient="Jane Example", channel="email", step=9, revision=revision)

    batch = await service.write_letters(item, strategy, target)

    assert batch.letters == []
    assert [s.reason for s in batch.skipped] == ["step 9 is no longer in the approach plan"]
    assert "expert-copywriter" not in runner.payloads


async def test_replanning_rewrites_only_the_steps_not_done(
    session: Session, item: Item, strategy: Strategy, tmp_path: Path
) -> None:
    script = outreach_script()
    # The second plan repeats the first; its day-0 touch clashes with the done one and is dropped.
    script["outreach-approach-planner"] *= 2
    script["outreach-personality-profiler"] *= 2
    script["expert-copywriter"] += script["expert-copywriter"][1:]
    script["expert-naturalizer"] += script["expert-naturalizer"][1:]
    runner = RecordedRunner(script)
    service = BizOutreach(session, PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path))
    await service.enrich(item, strategy)
    await service.plan_approach(item, strategy)
    await service.write_letters(item, strategy)
    opener = DraftRepository(session).for_item(item.id)[0]
    ApproachProgress(session).mark_done(item, 1, date.today())

    replanned = await service.plan_approach(item, strategy)
    await service.write_letters(item, strategy)

    assert [(t.step, t.day) for t in replanned.plan.touches] == [(1, 0), (2, 4), (3, 11)]
    assert [d.step for d in replanned.done] == [1]
    versions = [(d.step, d.version) for d in DraftRepository(session).for_item(item.id)]
    assert versions == [(1, 1), (2, 1), (2, 2), (3, 1), (3, 2)]
    rewritten = runner.payloads["expert-copywriter"][3]
    assert isinstance(rewritten, LetterBrief)
    assert rewritten.touch
    assert rewritten.touch.earlier[0].text == opener.body
