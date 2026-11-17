import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest
from playwright.async_api import Frame, Page

from mozg_outreach.apply import filler, form
from mozg_outreach.apply.filler import FormFiller
from mozg_outreach.apply.form import MARK, FormError, SafeActor, scan
from mozg_outreach.apply.models import AnswerInput, AnswerSet
from tests.apply.conftest import EMBED, PROFILE, ScriptedAnswerer, Traffic, application
from tests.browser.conftest import requires_chrome

pytestmark = requires_chrome

WHY = "Why do you want to work at Acme? *"


def form_of(page: Page) -> Frame:
    return next(f for f in page.frames if f.url == EMBED)


class TestGreenhouseEmbed:
    async def test_fills_standard_fields_files_and_answers_in_the_iframe(
        self, page: tuple[Page, Traffic], tmp_path: Path
    ) -> None:
        tab, traffic = page
        await tab.goto("https://careers.acme.example/jobs/1")
        answerer = ScriptedAnswerer(
            {WHY: "Real-time pricing is what I build.", "Years of Python": "6+"}
        )

        report = await FormFiller(answerer).fill(tab, application(), tmp_path)

        form = form_of(tab)
        assert report.ats == "greenhouse"
        assert await form.input_value("#first_name") == "Max"
        assert await form.input_value("#email") == "max@example.com"
        assert await form.input_value("#q1") == "Real-time pricing is what I build."
        assert await form.input_value("#q2") == "13"
        files = await form.eval_on_selector(
            "#resume", "el => [...el.files].map(f => [f.name, f.size])"
        )
        assert files == [["Max Example CV.pdf", len(application().cv_pdf or b"")]], (
            "the upload still has its bytes once filling is done"
        )
        letter = await form.eval_on_selector("#cover_letter", "el => el.files[0].name")
        assert letter == "Max Example cover letter.txt"
        photo = await form.eval_on_selector("#photo", "el => el.files[0].name")
        assert photo == "Max Example photo.png"
        assert await form.input_value("#country") == "GB"
        assert {f.source for f in report.filled} == {"profile", "cv", "letter", "answerer"}
        assert not [r for r in traffic.requests if r.startswith("POST")]

    async def test_protected_questions_stay_empty_and_never_reach_the_agent(
        self, page: tuple[Page, Traffic], tmp_path: Path
    ) -> None:
        tab, _ = page
        await tab.goto("https://careers.acme.example/jobs/1")
        answerer = ScriptedAnswerer({})

        report = await FormFiller(answerer).fill(tab, application(), tmp_path)

        asked = {q.label for q in answerer.asked[0].questions}
        assert asked == {WHY, "Years of Python"}
        open_labels = {f.label: f.reason for f in report.for_user}
        assert open_labels["Notice period"] == "your profile has no notice period"
        assert "demographic" in open_labels["Gender"]
        assert (
            "work authorization" in open_labels["Are you legally authorized to work in the UK? *"]
        )
        assert "consents" in open_labels["I consent to Acme storing my data"]
        form = form_of(tab)
        assert await form.input_value("#gender") == ""
        assert not await form.is_checked("input[name=gdpr]")

    async def test_a_rejected_answer_is_left_with_the_reason(
        self, page: tuple[Page, Traffic], tmp_path: Path
    ) -> None:
        tab, _ = page
        await tab.goto("https://careers.acme.example/jobs/1")
        answerer = ScriptedAnswerer({"Years of Python": "about 7"})

        report = await FormFiller(answerer).fill(tab, application(letter=None), tmp_path)

        reasons = {f.label: f.reason for f in report.for_user}
        assert "'about 7' is not one of the options" in reasons["Years of Python"]
        assert reasons["Cover Letter"] == "no cover letter draft yet"
        assert reasons[WHY] == "the CV doesn't answer it"


async def test_lever_fills_by_name_and_leaves_dropdowns_and_pay(
    page: tuple[Page, Traffic], tmp_path: Path
) -> None:
    tab, _ = page
    await tab.goto("https://jobs.lever.co/acme/1234/apply")
    answerer = ScriptedAnswerer({"Preferred work mode": "Remote"})

    report = await FormFiller(answerer).fill(tab, application(), tmp_path)

    assert report.ats == "lever"
    assert await tab.input_value("input[name=name]") == "Max Example"
    assert await tab.input_value("input[name='urls[LinkedIn]']") == PROFILE.linkedin
    assert await tab.is_checked("input[value=remote]")
    reasons = {f.label: f.reason for f in report.for_user}
    assert "custom dropdown" in reasons["Office location"]
    assert "pay" in reasons["Expected salary"]
    assert reasons["Current company"] == "your profile has no current company"


async def test_workday_is_left_to_the_applicant(page: tuple[Page, Traffic], tmp_path: Path) -> None:
    tab, _ = page
    await tab.goto("https://acme.wd3.myworkdayjobs.com/en-US/careers/job/1")

    report = await FormFiller(ScriptedAnswerer({})).fill(tab, application(), tmp_path)

    assert report.filled == []
    assert "account per employer" in report.for_user[0].reason
    assert await tab.locator(f"[{MARK}]").count() == 0


async def test_a_form_that_submits_itself_is_stopped(
    page: tuple[Page, Traffic], tmp_path: Path
) -> None:
    tab, traffic = page
    await tab.goto("https://careers.acme.example/apply")

    report = await FormFiller(ScriptedAnswerer({"Team": "Data"})).fill(tab, application(), tmp_path)
    # The page's submit used to start after the guard was gone; give it time to show up.
    await tab.wait_for_timeout(500)

    assert not [r for r in traffic.requests if r.startswith("POST")]
    assert await tab.input_value("select[name=team]") == "data"
    assert any("Blocked a submission" in c for c in report.concerns)
    assert tab.url == "https://careers.acme.example/apply"


async def test_the_guard_is_gone_when_the_applicant_submits(
    page: tuple[Page, Traffic], tmp_path: Path
) -> None:
    tab, traffic = page
    await tab.goto("https://jobs.lever.co/acme/1234/apply")
    await FormFiller(ScriptedAnswerer({})).fill(tab, application(), tmp_path)

    await tab.click("text=Submit")
    await tab.wait_for_load_state()

    assert "POST https://jobs.lever.co/apply" in traffic.requests


async def test_fields_the_page_hides_are_neither_asked_nor_filled(
    page: tuple[Page, Traffic],
    tmp_path: Path,
) -> None:
    tab, _ = page
    await tab.goto("https://careers.acme.example/jobs/2")
    answerer = ScriptedAnswerer({"Why Acme?": "Pricing.", "Open to relocation?": "yes"})

    report = await FormFiller(answerer).fill(tab, application(), tmp_path)

    [asked] = answerer.asked
    assert [q.label for q in asked.questions] == ["Open to relocation?", "Why Acme?"]
    filled = {f.label for f in report.filled}
    assert filled == {"Full name", "Email", "Resume/CV", "Open to relocation?", "Why Acme?"}
    assert await tab.eval_on_selector("input[name=resume]", "el => el.files.length") == 1
    assert await tab.is_checked("input[name=relocate]"), "a styled checkbox is still ticked"
    reasons = {f.label: f.reason for f in report.for_user}
    assert reasons["Writing sample"] == "an upload only you can choose", (
        "and the answerer isn't asked"
    )
    assert set(reasons).isdisjoint({"Your email", "Weekly digest", "Keyword", "Coupon"})
    assert "Portfolio PDF" not in reasons, "an upload in a hidden tab isn't part of the form"


class TestTimeLimits:
    async def test_a_field_that_never_becomes_editable_is_left_quickly(
        self, page: tuple[Page, Traffic], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(form, "ACTION_MS", 300)
        tab, _ = page
        await tab.goto("https://jobs.lever.co/acme/1234/apply")
        fields = await scan(tab.main_frame)
        name = next(f for f in fields if f.label == "Full name")
        await tab.eval_on_selector("input[name=name]", "el => { el.style.display = 'none'; }")

        with pytest.raises(FormError, match=r"could not be filled within 0\.3 s"):
            await asyncio.wait_for(SafeActor(tab.main_frame).fill(name, "Max"), timeout=5)

    async def test_fields_left_when_the_budget_runs_out_go_to_the_applicant(
        self, page: tuple[Page, Traffic], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(filler, "FILL_SECONDS", 0.0)
        tab, _ = page
        await tab.goto("https://jobs.lever.co/acme/1234/apply")

        report = await FormFiller(ScriptedAnswerer({})).fill(tab, application(), tmp_path)

        assert report.filled == []
        reasons = {f.label: f.reason for f in report.for_user}
        assert "ran out of time" in reasons["Full name"]
        assert await tab.input_value("input[name=name]") == ""

    async def test_the_answerer_thinking_does_not_use_up_the_budget(
        self, page: tuple[Page, Traffic], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(filler, "FILL_SECONDS", 1.0)
        tab, _ = page
        await tab.goto("https://jobs.lever.co/acme/1234/apply")
        answerer = SlowAnswerer({"Preferred work mode": "Remote"}, delay=1.5)

        report = await FormFiller(answerer).fill(tab, application(), tmp_path)

        assert "Preferred work mode" in {f.label for f in report.filled}
        assert await tab.is_checked("input[value=remote]")


@dataclass
class SlowAnswerer(ScriptedAnswerer):
    delay: float = 0.0

    async def answer(self, request: AnswerInput) -> AnswerSet:
        await asyncio.sleep(self.delay)
        return await super().answer(request)
