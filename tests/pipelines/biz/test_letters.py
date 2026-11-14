from pathlib import Path

import pytest

from mozg_outreach.agents import AgentRunError, PromptLibrary
from mozg_outreach.letters.models import LetterBrief, LetterDraft, SenderSettings
from mozg_outreach.naturalize.models import NaturalizeResult
from mozg_outreach.naturalize.naturalizer import Naturalizer
from mozg_outreach.naturalize.validation import body_word_count
from mozg_outreach.pipelines.biz.enrichment_models import CompanyDossier
from mozg_outreach.pipelines.biz.letter_briefs import angles_from
from mozg_outreach.pipelines.biz.letters import (
    BizLetterWriter,
    LetterBatch,
    LetterRequest,
)
from mozg_outreach.pipelines.biz.models import Strategy
from mozg_outreach.pipelines.steps import PipelineSteps
from tests.pipelines.biz.conftest import BODY, HOOK, STYLE
from tests.pipelines.conftest import REPO_PROMPTS, RecordedRunner, Scripted


def draft(
    body: str = BODY, subject: str | None = "Katana's platform load after the round"
) -> LetterDraft:
    return LetterDraft(subject=subject, body=body, hook_used=HOOK, angle_used="cost", concerns=[])


def naturalized(text: str) -> NaturalizeResult:
    return NaturalizeResult(
        text=text, changes=["Plainer verbs."], specificity_needed=[], concerns=[]
    )


@pytest.fixture
def request_(dossier: CompanyDossier, strategy: Strategy) -> LetterRequest:
    return LetterRequest(
        company="Katana",
        fields={},
        dossier=dossier,
        angles=angles_from(strategy.outreach_angles),
        sender=SenderSettings(name="Stan Petrov", tone="warm", voice="plain, direct"),
    )


async def write(
    request: LetterRequest, script: dict[str, list[Scripted]], tmp_path: Path
) -> tuple[LetterBatch, RecordedRunner]:
    runner = RecordedRunner(script)
    steps = PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path)
    return await BizLetterWriter(steps, Naturalizer(steps)).write(request), runner


def test_fixture_body_fits_the_email_budget() -> None:
    assert 120 <= body_word_count(f"Hi Jane,\n\n{BODY}\n\nThanks,\nStan") <= 180


async def test_selected_company_gets_a_framed_naturalized_letter(
    request_: LetterRequest, tmp_path: Path
) -> None:
    final = f"Hi Jane,\n\n{BODY.replace('Congrats on', 'Congratulations on')}\n\nThanks,\nStan"
    batch, runner = await write(
        request_,
        {
            "outreach-personality-profiler": [STYLE],
            "expert-copywriter": [draft()],
            "expert-naturalizer": [naturalized(final)],
        },
        tmp_path,
    )

    [letter] = batch.letters
    assert batch.skipped == []
    assert (letter.recipient, letter.channel) == ("Jane Example", "email")
    assert letter.text == final
    assert letter.subject == "Katana's platform load after the round"
    review = letter.review
    assert (review.address, review.address_confidence) == ("hello@katanamrp.com", "shared_inbox")
    assert review.naturalized
    assert "Congrats on" in review.before_naturalize
    assert review.changes == ["Plainer verbs."]
    assert review.problems == []
    assert review.reader_calibration == "role priorities: technical fit, reliability"
    [brief] = runner.payloads["expert-copywriter"]
    assert isinstance(brief, LetterBrief)
    assert brief.generic_inbox
    assert brief.reader_style == STYLE
    assert brief.hooks[0].signal.startswith(HOOK)


async def test_invented_number_gets_one_retry_with_the_problem(
    request_: LetterRequest, tmp_path: Path
) -> None:
    invented = BODY.replace("keep cloud spend flat", "cut cloud spend by 37%")
    clean_text = f"Hi Jane,\n\n{BODY}\n\nThanks,\nStan"
    batch, runner = await write(
        request_,
        {
            "outreach-personality-profiler": [STYLE],
            "expert-copywriter": [draft(invented), draft()],
            "expert-naturalizer": [naturalized(clean_text)],
        },
        tmp_path,
    )

    retry_brief = runner.payloads["expert-copywriter"][1]
    assert isinstance(retry_brief, LetterBrief)
    assert retry_brief.retry
    assert retry_brief.retry.problems == ["'37' is not in the evidence"]
    assert batch.letters[0].review.problems == []


async def test_problems_left_after_retry_are_shown_not_hidden(
    request_: LetterRequest, tmp_path: Path
) -> None:
    long_subject = draft(subject="x" * 60)
    batch, _ = await write(
        request_,
        {
            "outreach-personality-profiler": [
                AgentRunError("outreach-personality-profiler", "error_max_turns", "timed out")
            ],
            "expert-copywriter": [long_subject, long_subject],
            "expert-naturalizer": [
                AgentRunError("expert-naturalizer", "error_during_execution", "down")
            ],
        },
        tmp_path,
    )

    review = batch.letters[0].review
    assert review.problems == ["subject has 60 characters, limit 55"]
    assert review.reader_calibration == "neutral: the profiler failed"
    assert not review.naturalized
    assert any("Naturalizer failed" in note for note in review.review_notes)


async def test_copywriter_failure_skips_only_that_letter(
    request_: LetterRequest, tmp_path: Path
) -> None:
    batch, _ = await write(
        request_,
        {
            "outreach-personality-profiler": [STYLE],
            "expert-copywriter": [
                AgentRunError("expert-copywriter", "error_during_execution", "rate limited")
            ],
        },
        tmp_path,
    )
    assert batch.letters == []
    assert [s.reason for s in batch.skipped] == [
        "copywriter failed: expert-copywriter: run failed (error_during_execution): rate limited"
    ]


async def test_no_verified_hook_means_no_agent_calls(
    request_: LetterRequest, tmp_path: Path
) -> None:
    contacts_only = CompanyDossier(contacts=request_.dossier.contacts)
    bare = request_.model_copy(update={"dossier": contacts_only})
    batch, runner = await write(bare, {}, tmp_path)
    assert batch.letters == []
    assert [s.reason for s in batch.skipped] == ["no verified fact about the company to open with"]
    assert runner.payloads == {}
