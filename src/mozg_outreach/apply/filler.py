"""Fills one application form for the applicant to review and submit by hand."""

import asyncio
import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import Page

from mozg_outreach.apply.answers import Answerer, check, question
from mozg_outreach.apply.ats import Ats, standard_key
from mozg_outreach.apply.form import FormError, SafeActor, SubmitGuard, locate_form, scan
from mozg_outreach.apply.models import (
    AnswerInput,
    ApplicantProfile,
    FilledField,
    FillReport,
    FillSource,
    FormField,
    OpenField,
)
from mozg_outreach.apply.policy import protected_reason

logger = logging.getLogger(__name__)

# Past this, the fields still open go to the applicant so the run never stalls on one page.
FILL_SECONDS = 90.0


@dataclass(frozen=True)
class Application:
    profile: ApplicantProfile
    job_title: str
    company: str
    job_summary: str
    cv_text: str
    cv_pdf: bytes | None
    letter: str | None


class FormFiller:
    def __init__(self, answerer: Answerer) -> None:
        self._answerer = answerer

    async def fill(self, page: Page, application: Application) -> FillReport:
        ats, frame = await locate_form(page)
        if ats.manual:
            return FillReport(
                ats=ats.name,
                url=page.url,
                filled=[],
                for_user=[
                    OpenField(
                        label="The whole form",
                        required=True,
                        reason=f"{ats.name} needs an account per employer, so it is left to you",
                    )
                ],
                concerns=[],
            )
        fields = await scan(frame)
        logger.info("%s form at %s: %d fields", ats.name, page.url, len(fields))
        run = _Fill(SafeActor(frame), application)
        with tempfile.TemporaryDirectory(prefix="mozg-apply-") as files:
            async with SubmitGuard(page, frame) as guard:
                questions = await run.standard(fields, ats, Path(files))
                await run.answers(questions, self._answerer)
        concerns = run.concerns + [
            f"Blocked a submission while filling: {b}" for b in guard.blocked
        ]
        if not fields:
            concerns.append("No form fields were found on the page.")
        return FillReport(
            ats=ats.name, url=page.url, filled=run.filled, for_user=run.for_user, concerns=concerns
        )


class _Fill:
    """One form's progress: what was filled, what waits for the applicant."""

    def __init__(self, actor: SafeActor, application: Application) -> None:
        self._actor = actor
        self._app = application
        self._deadline = asyncio.get_running_loop().time() + FILL_SECONDS
        self.filled: list[FilledField] = []
        self.for_user: list[OpenField] = []
        self.concerns: list[str] = []

    async def standard(self, fields: list[FormField], ats: Ats, files: Path) -> list[FormField]:
        """Fills what the profile, CV and letter cover; returns the questions left over."""
        left: list[FormField] = []
        for form_field in fields:
            reason = protected_reason(form_field)
            if reason:
                self._leave(form_field, reason)
                continue
            if form_field.kind == "combobox":
                self._leave(form_field, "a custom dropdown the service can't pick safely")
                continue
            key = standard_key(ats, form_field)
            if key is None:
                left.append(form_field)
            elif key == "resume":
                await self._attach_cv(form_field, files)
            elif key == "cover_letter":
                await self._letter(form_field, files)
            else:
                value = self._app.profile.value(key)
                if value:
                    await self._put(form_field, value, "profile")
                else:
                    self._leave(form_field, f"your profile has no {key.replace('_', ' ')}")
        return left

    async def answers(self, questions: list[FormField], answerer: Answerer) -> None:
        if not questions:
            return
        by_id = {q.id: q for q in questions}
        loop = asyncio.get_running_loop()
        asked_at = loop.time()
        result = await answerer.answer(
            AnswerInput(
                job_title=self._app.job_title,
                company=self._app.company,
                job_summary=self._app.job_summary,
                cv_text=self._app.cv_text,
                questions=[question(q) for q in questions],
            )
        )
        # The budget is for the page; the agent's thinking time doesn't count against it.
        self._deadline += loop.time() - asked_at
        self.concerns.extend(result.concerns)
        answered: set[str] = set()
        for answer in result.answers:
            form_field = by_id.get(answer.question_id)
            if form_field is None or answer.question_id in answered:
                continue
            checked = check(form_field, answer)
            if checked.value is None:
                self._leave(form_field, f"the drafted answer was rejected: {checked.problem}")
            else:
                await self._put(form_field, checked.value, "answerer")
            answered.add(answer.question_id)
        for form_field in questions:
            if form_field.id not in answered:
                self._leave(form_field, "the CV doesn't answer it")

    async def _attach_cv(self, form_field: FormField, files: Path) -> None:
        if self._app.cv_pdf is None:
            self._leave(form_field, "no CV PDF is stored")
            return
        path = files / f"{_file_stem(self._app.profile)} CV.pdf"
        path.write_bytes(self._app.cv_pdf)
        await self._attach(form_field, path, "cv")

    async def _letter(self, form_field: FormField, files: Path) -> None:
        if not self._app.letter:
            self._leave(form_field, "no cover letter draft yet")
        elif form_field.kind == "file":
            path = files / f"{_file_stem(self._app.profile)} cover letter.txt"
            path.write_text(self._app.letter, encoding="utf-8")
            await self._attach(form_field, path, "letter")
        else:
            await self._put(form_field, self._app.letter, "letter")

    async def _put(self, form_field: FormField, value: str, source: FillSource) -> None:
        if self._out_of_time(form_field):
            return
        try:
            await self._actor.fill(form_field, value)
        except FormError as exc:
            self._leave(form_field, str(exc))
            return
        self.filled.append(FilledField(label=form_field.label, source=source))

    async def _attach(self, form_field: FormField, path: Path, source: FillSource) -> None:
        if self._out_of_time(form_field):
            return
        try:
            await self._actor.attach(form_field, path)
        except FormError as exc:
            self._leave(form_field, str(exc))
            return
        self.filled.append(FilledField(label=form_field.label, source=source))

    def _out_of_time(self, form_field: FormField) -> bool:
        if asyncio.get_running_loop().time() < self._deadline:
            return False
        self._leave(form_field, f"filling ran out of time after {FILL_SECONDS:g} s")
        return True

    def _leave(self, form_field: FormField, reason: str) -> None:
        self.for_user.append(
            OpenField(label=form_field.label, required=form_field.required, reason=reason)
        )


def _file_stem(profile: ApplicantProfile) -> str:
    return (
        re.sub(r"[^\w .-]", "", f"{profile.first_name} {profile.last_name}").strip() or "Applicant"
    )
