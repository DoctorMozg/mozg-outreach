"""Fills one application form for the applicant to review and submit by hand."""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from playwright.async_api import Frame, Page

from mozg_outreach.apply.answers import CHOICE_KINDS, ISO_DATE, Answerer, check, question
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
    ProfileKey,
    decode_photo,
)
from mozg_outreach.apply.policy import protected_reason

logger = logging.getLogger(__name__)

# Past this, the fields still open go to the applicant so the run never stalls on one page.
FILL_SECONDS = 90.0
# An upload that fills the form from a resume, like Ashby's "Autofill from resume".
AUTOFILL = re.compile(r"\bauto-?fill")
AUTOFILL_SECONDS = 20.0
AUTOFILL_POLL_SECONDS = 1.0


@dataclass(frozen=True)
class Application:
    profile: ApplicantProfile
    job_title: str
    company: str
    job_summary: str
    preferences: str
    cv_text: str
    cv_pdf: bytes | None
    letter: str | None


class FormFiller:
    def __init__(self, answerer: Answerer) -> None:
        self._answerer = answerer

    async def fill(self, page: Page, application: Application, files: Path) -> FillReport:
        """Fills the form; uploads are written to `files`, which must outlive the submit."""
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
        async with SubmitGuard(page, frame) as guard:
            fields = await run.autofill(frame, fields, files)
            questions = await run.standard(fields, ats, files)
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
            if form_field.filled:
                # The page or its resume autofill answered it; only what is still empty is ours.
                if all(f.label != form_field.label for f in self.filled):
                    self.filled.append(FilledField(label=form_field.label, source="page"))
                continue
            if form_field.kind == "combobox":
                self._leave(form_field, "a custom dropdown the service can't pick safely")
                continue
            key = standard_key(ats, form_field)
            if key is None and form_field.kind == "file":
                self._leave(form_field, "an upload only you can choose")
            elif key is None:
                left.append(form_field)
            elif key == "resume":
                await self._attach_cv(form_field, files)
            elif key == "cover_letter":
                await self._letter(form_field, files)
            elif key == "photo":
                await self._attach_photo(form_field, files)
            elif not await self._profile_value(form_field, key):
                left.append(form_field)
        return left

    async def autofill(self, frame: Frame, fields: list[FormField], files: Path) -> list[FormField]:
        """Lets the page fill what it can from the CV first; returns the form as it is then."""
        upload = next(
            (f for f in fields if f.kind == "file" and AUTOFILL.search(f.label.casefold())), None
        )
        if upload is None or self._app.cv_pdf is None or not await self._attach_cv(upload, files):
            return fields
        loop = asyncio.get_running_loop()
        started = loop.time()
        settled = await _settled(frame)
        # Waiting for the page's parser is not filling time.
        self._deadline += loop.time() - started
        return settled

    async def _profile_value(self, form_field: FormField, key: ProfileKey) -> bool:
        """Fills a detail the profile has in a shape the field takes; False hands it on."""
        value = self._app.profile.value(key)
        if not value:
            return False
        shown = value
        if form_field.kind in CHOICE_KINDS:
            option = _option(form_field, value)
            if option is None:
                return False
            value = option
        elif form_field.kind == "date" and not ISO_DATE.fullmatch(value):
            return False
        await self._put(
            form_field, value, FilledField(label=form_field.label, source="profile", value=shown)
        )
        return True

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
                today=date.today(),
                applicant=self._app.profile.facts(),
                preferences=self._app.preferences,
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
                reasoning = answer.basis if answer.reasoned else None
                filled = FilledField(
                    label=form_field.label,
                    source="answerer",
                    value=answer.value,
                    reasoning=reasoning,
                )
                await self._put(form_field, checked.value, filled)
            answered.add(answer.question_id)
        for form_field in questions:
            if form_field.id not in answered:
                self._leave(form_field, "the AI gave no answer")

    async def _attach_cv(self, form_field: FormField, files: Path) -> bool:
        if self._app.cv_pdf is None:
            self._leave(form_field, "no CV PDF is stored")
            return False
        path = files / f"{_file_stem(self._app.profile)} CV.pdf"
        path.write_bytes(self._app.cv_pdf)
        return await self._attach(form_field, path, "cv")

    async def _attach_photo(self, form_field: FormField, files: Path) -> None:
        if self._app.profile.photo is None:
            self._leave(form_field, "your profile has no photo")
            return
        photo = decode_photo(self._app.profile.photo)
        path = files / f"{_file_stem(self._app.profile)} photo.{photo.extension}"
        path.write_bytes(photo.data)
        await self._attach(form_field, path, "profile")

    async def _letter(self, form_field: FormField, files: Path) -> None:
        if not self._app.letter:
            self._leave(form_field, "no cover letter draft yet")
        elif form_field.kind == "file":
            path = files / f"{_file_stem(self._app.profile)} cover letter.txt"
            path.write_text(self._app.letter, encoding="utf-8")
            await self._attach(form_field, path, "letter")
        else:
            await self._put(
                form_field, self._app.letter, FilledField(label=form_field.label, source="letter")
            )

    async def _put(self, form_field: FormField, value: str, filled: FilledField) -> None:
        if self._out_of_time(form_field):
            return
        try:
            await self._actor.fill(form_field, value)
        except FormError as exc:
            self._leave(form_field, str(exc))
            return
        self.filled.append(filled)

    async def _attach(self, form_field: FormField, path: Path, source: FillSource) -> bool:
        if self._out_of_time(form_field):
            return False
        try:
            await self._actor.attach(form_field, path)
        except FormError as exc:
            self._leave(form_field, str(exc))
            return False
        self.filled.append(FilledField(label=form_field.label, source=source))
        return True

    def _out_of_time(self, form_field: FormField) -> bool:
        if asyncio.get_running_loop().time() < self._deadline:
            return False
        self._leave(form_field, f"filling ran out of time after {FILL_SECONDS:g} s")
        return True

    def _leave(self, form_field: FormField, reason: str) -> None:
        self.for_user.append(
            OpenField(label=form_field.label, required=form_field.required, reason=reason)
        )


async def _settled(frame: Frame) -> list[FormField]:
    """Rescans until the page's autofill has added values and stopped, or the wait runs out."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + AUTOFILL_SECONDS
    fields = await scan(frame)
    start = last = sum(f.filled for f in fields)
    while loop.time() < deadline:
        await asyncio.sleep(AUTOFILL_POLL_SECONDS)
        fields = await scan(frame)
        count = sum(f.filled for f in fields)
        if count > start and count == last:
            logger.info("resume autofill filled %d field(s)", count - start)
            return fields
        last = count
    logger.info("resume autofill added nothing within %g s", AUTOFILL_SECONDS)
    return fields


def _option(form_field: FormField, value: str) -> str | None:
    wanted = value.casefold()
    return next(
        (o.value for o in form_field.options if wanted in (o.label.casefold(), o.value.casefold())),
        None,
    )


def _file_stem(profile: ApplicantProfile) -> str:
    return (
        re.sub(r"[^\w .-]", "", f"{profile.first_name} {profile.last_name}").strip() or "Applicant"
    )
