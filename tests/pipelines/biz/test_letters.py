from pathlib import Path
from typing import Literal

import pytest

from mozg_outreach.agents import AgentRunError, PromptLibrary
from mozg_outreach.letters.frame import WORDINGS
from mozg_outreach.letters.models import (
    LetterBrief,
    LetterDraft,
    Revision,
    RevisionTarget,
    SenderSettings,
)
from mozg_outreach.naturalize.models import NaturalizeResult
from mozg_outreach.naturalize.naturalizer import Naturalizer
from mozg_outreach.naturalize.validation import body_word_count
from mozg_outreach.pipelines.biz.approach_models import (
    ApproachPlan,
    PlanContact,
    PlannedTouch,
    PlanRoute,
    StoredApproach,
)
from mozg_outreach.pipelines.biz.enrichment_models import CompanyDossier
from mozg_outreach.pipelines.biz.letter_briefs import angles_from
from mozg_outreach.pipelines.biz.letters import (
    BizLetterWriter,
    LetterBatch,
    LetterRequest,
    RegionRules,
)
from mozg_outreach.pipelines.biz.models import Strategy
from mozg_outreach.pipelines.steps import PipelineSteps
from tests.pipelines.biz.conftest import BODY, HOOK, STYLE
from tests.pipelines.conftest import REPO_PROMPTS, RecordedRunner, Scripted


def draft(body: str = BODY, subject: str | None = "Katana's platform load") -> LetterDraft:
    return LetterDraft(subject=subject, body=body, hook_used=HOOK, angle_used="cost", concerns=[])


def naturalized(text: str) -> NaturalizeResult:
    return NaturalizeResult(
        text=text, changes=["Plainer verbs."], specificity_needed=[], concerns=[]
    )


def stop_offered(letter: str) -> str:
    """A first email: the opt-out line goes above the sign-off."""
    head, ending = letter.rsplit("\n\n", 1)
    return f"{head}\n\n{WORDINGS['en'].opt_out}\n\n{ending}"


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
    return await BizLetterWriter(steps, Naturalizer(steps), candidates=1).write(request), runner


def test_fixture_body_fits_the_email_budget() -> None:
    assert 60 <= body_word_count(f"Hi Jane,\n\n{BODY}\n\nThanks,\nStan") <= 100


async def test_company_gets_a_framed_naturalized_letter(
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
    assert letter.text == stop_offered(final)
    assert letter.subject == "Katana's platform load"
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


async def test_an_inbox_touch_to_no_one_greets_whoever_reads_it(
    request_: LetterRequest, tmp_path: Path
) -> None:
    inbox = PlanContact(
        ref="inbox",
        name=None,
        title="Company inbox",
        relevance="",
        routes=[
            PlanRoute(
                ref="inbox.email1",
                channel="email",
                address="hello@katanamrp.com",
                confidence="shared_inbox",
            )
        ],
    )
    touch = PlannedTouch(
        step=1,
        day=0,
        contact_ref="inbox",
        route_ref="inbox.email1",
        purpose="open",
        angle="cost",
        hook_ref=None,
        ask="Who looks after platform costs?",
        talking_points="",
    )
    plan = ApproachPlan(
        summary="", first_contact_ref="inbox", rationale="", touches=[touch], avoid=[], concerns=[]
    )
    approach = StoredApproach(plan=plan, contacts=[inbox], hooks=[], planned_on="2026-11-16")
    final = f"Hello,\n\n{BODY}\n\nThanks,\nStan"

    batch, runner = await write(
        request_.model_copy(update={"approach": approach}),
        {"expert-copywriter": [draft()], "expert-naturalizer": [naturalized(final)]},
        tmp_path,
    )

    [letter] = batch.letters
    assert (letter.recipient, letter.step, letter.text) == ("Katana team", 1, stop_offered(final))
    assert "outreach-personality-profiler" not in runner.payloads, "no one to profile"


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
    long_subject = draft(subject=" ".join(["x" * 14] * 4))
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
    assert review.problems == ["subject has 59 characters, limit 55"]
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


class TestRevision:
    REVISION = Revision(
        previous_text="Hi Jane,\n\nOld body.\n\nThanks,\nStan", suggestion="Shorter."
    )

    async def test_rewrites_the_one_letter_with_the_suggestion(
        self, request_: LetterRequest, tmp_path: Path
    ) -> None:
        target = RevisionTarget(recipient="Jane Example", channel="email", revision=self.REVISION)
        final = f"Hi Jane,\n\n{BODY}\n\nThanks,\nStan"
        batch, runner = await write(
            request_.model_copy(update={"revision": target}),
            {
                "outreach-personality-profiler": [STYLE],
                "expert-copywriter": [draft()],
                "expert-naturalizer": [naturalized(final)],
            },
            tmp_path,
        )
        assert [letter.recipient for letter in batch.letters] == ["Jane Example"]
        [brief] = runner.payloads["expert-copywriter"]
        assert isinstance(brief, LetterBrief)
        assert brief.revision == self.REVISION

    async def test_a_contact_no_longer_reachable_is_skipped(
        self, request_: LetterRequest, tmp_path: Path
    ) -> None:
        target = RevisionTarget(recipient="Gone Person", channel="email", revision=self.REVISION)
        batch, runner = await write(request_.model_copy(update={"revision": target}), {}, tmp_path)
        assert batch.letters == []
        assert [(s.contact, s.reason) for s in batch.skipped] == [
            ("Gone Person", "no email route to them in the dossier any more")
        ]
        assert runner.payloads == {}


async def test_a_second_persons_first_letter_knows_who_heard_first(
    request_: LetterRequest, tmp_path: Path
) -> None:
    people = [
        PlanContact(
            ref=ref,
            name=name,
            title="Partner",
            relevance="",
            routes=[
                PlanRoute(
                    ref=f"{ref}.email",
                    channel="email",
                    address=f"{ref}@x.example",
                    confidence="published",
                )
            ],
        )
        for ref, name in (("c1", "Ann Lee"), ("c2", "Bob Ray"))
    ]
    touches = [
        PlannedTouch(
            step=step,
            day=day,
            contact_ref=ref,
            route_ref=f"{ref}.email",
            purpose="open" if step < 3 else "follow_up",
            angle="cost",
            hook_ref=None,
            ask="Worth a look?",
            talking_points="",
        )
        for step, day, ref in ((1, 0, "c1"), (2, 3, "c2"), (3, 6, "c2"))
    ]
    plan = ApproachPlan(
        summary="", first_contact_ref="c1", rationale="", touches=touches, avoid=[], concerns=[]
    )
    approach = StoredApproach(plan=plan, contacts=people, hooks=[], planned_on="2026-11-16")
    script: dict[str, list[Scripted]] = {
        "outreach-personality-profiler": [STYLE, STYLE],
        "expert-copywriter": [draft(), draft(), draft(subject=None)],
        "expert-naturalizer": [naturalized("x")] * 6,
    }

    _, runner = await write(request_.model_copy(update={"approach": approach}), script, tmp_path)

    briefs = [b for b in runner.payloads["expert-copywriter"] if isinstance(b, LetterBrief)]
    seen = {(b.recipient.name, b.touch.step): b.touch.others_contacted for b in briefs if b.touch}
    assert seen == {("Ann Lee", 1): [], ("Bob Ray", 2): ["Ann Lee"], ("Bob Ray", 3): []}


NOTE = (
    "Congratulations on the Katana platform launch. I build tooling for manufacturing teams "
    "in Dubai and would like to follow how it goes."
)
# Within the word budget, yet past 200 characters once framed.
LONG_NOTE = (
    "Congratulations on the remarkable manufacturing platform expansion announcement. "
    "Manufacturing operations conversations interest me considerably, particularly "
    "infrastructure modernization, distributed warehouse orchestration and transformation."
)


type Connection = Literal["note", "no_note"] | None


class TestConnectionRequests:
    @pytest.fixture
    def linkedin(self) -> PlanContact:
        return PlanContact(
            ref="c1",
            name="Ann Lee",
            title="CEO",
            relevance="",
            routes=[
                PlanRoute(
                    ref="c1.linkedin",
                    channel="linkedin",
                    address="https://www.linkedin.com/in/ann-lee",
                    confidence="linkedin",
                )
            ],
        )

    def approach(self, contact: PlanContact, *steps: tuple[int, Connection]) -> StoredApproach:
        touches = [
            PlannedTouch(
                step=step,
                day=3 * (step - 1),
                contact_ref="c1",
                route_ref="c1.linkedin",
                purpose="open",
                angle="cost",
                hook_ref=None,
                ask="",
                talking_points="Congratulate her on the launch.",
                connection=connection,
            )
            for step, connection in steps
        ]
        plan = ApproachPlan(
            summary="", first_contact_ref="c1", rationale="", touches=touches, avoid=[], concerns=[]
        )
        return StoredApproach(plan=plan, contacts=[contact], hooks=[], planned_on="2026-11-17")

    async def test_a_note_fits_linkedins_200_characters_and_offers_no_opt_out(
        self, request_: LetterRequest, linkedin: PlanContact, tmp_path: Path
    ) -> None:
        # Gulf readers get "Hello", not "Hi".
        final = f"Hello Ann,\n\n{NOTE}\n\nThanks,\nStan"
        long = f"Hello Ann,\n\n{LONG_NOTE}\n\nThanks,\nStan"
        assert len(long) > 200 >= len(final)
        request = request_.model_copy(
            update={
                "approach": self.approach(linkedin, (1, "note")),
                "region": RegionRules(gulf=True),
            }
        )
        script: dict[str, list[Scripted]] = {
            "outreach-personality-profiler": [STYLE],
            "expert-copywriter": [draft(LONG_NOTE, subject=None), draft(NOTE, subject=None)],
            "expert-naturalizer": [naturalized(final)],
        }

        batch, runner = await write(request, script, tmp_path)

        [letter] = batch.letters
        # No opt-out line, though Gulf letters otherwise always carry one.
        assert letter.text == final
        first, retry = [
            b for b in runner.payloads["expert-copywriter"] if isinstance(b, LetterBrief)
        ]
        assert first.max_chars == 200
        assert first.touch is not None
        assert first.touch.connection_note
        assert retry.retry is not None
        assert any("limit 200" in p for p in retry.retry.problems)

    async def test_a_request_without_a_note_gets_no_letter(
        self, request_: LetterRequest, linkedin: PlanContact, tmp_path: Path
    ) -> None:
        message = f"Hi Ann,\n\n{BODY}\n\nThanks,\nStan"
        request = request_.model_copy(
            update={"approach": self.approach(linkedin, (1, "no_note"), (2, None))}
        )
        script: dict[str, list[Scripted]] = {
            "outreach-personality-profiler": [STYLE],
            "expert-copywriter": [draft(subject=None)],
            "expert-naturalizer": [naturalized(message)],
        }

        batch, runner = await write(request, script, tmp_path)

        assert [letter.step for letter in batch.letters] == [2]
        [brief] = [b for b in runner.payloads["expert-copywriter"] if isinstance(b, LetterBrief)]
        assert brief.max_chars is None
        assert brief.touch is not None
        assert [(e.step, e.text) for e in brief.touch.earlier] == [(1, None)]
