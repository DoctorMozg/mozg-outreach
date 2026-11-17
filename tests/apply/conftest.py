from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from playwright.async_api import Page, Route, async_playwright

from mozg_outreach.apply.desk import ApplyAgents
from mozg_outreach.apply.filler import Application
from mozg_outreach.apply.models import (
    Answer,
    AnswerInput,
    AnswerSet,
    ApplicantProfile,
    FormFinderInput,
    FormFinding,
)

EMBED = "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=1"
PAGES = {
    "https://careers.acme.example/jobs/1": "greenhouse_page.html",
    EMBED: "greenhouse_form.html",
    "https://jobs.lever.co/acme/1234/apply": "lever_form.html",
    "https://acme.wd3.myworkdayjobs.com/en-US/careers/job/1": "workday.html",
    "https://careers.acme.example/apply": "autosubmit_form.html",
    "https://careers.acme.example/jobs/2": "listing_page.html",
    "https://careers.acme.example/styled": "styled_form.html",
    "https://careers.acme.example/autofill": "autofill_form.html",
}
PAGE_DIR = Path(__file__).parent / "pages"

# A 1x1 PNG.
PHOTO = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
PROFILE = ApplicantProfile(
    first_name="Max",
    last_name="Example",
    email="max@example.com",
    phone="+44 20 7946 0000",
    country="United Kingdom",
    linkedin="https://www.linkedin.com/in/max-example",
    photo=PHOTO,
)


def application(letter: str | None = "Dear Acme team, ...") -> Application:
    return Application(
        profile=PROFILE,
        job_title="Data Engineer",
        company="Acme",
        job_summary="Real-time pricing pipelines in Python.",
        preferences="Remote roles in data engineering, from EUR 90k a year.",
        cv_text="Max Example. Senior Data Engineer, 2019-2026. Python, Kafka.",
        cv_pdf=b"%PDF-1.4 fake",
        letter=letter,
    )


@dataclass
class Traffic:
    requests: list[str] = field(default_factory=list)


@pytest.fixture
async def page() -> AsyncIterator[tuple[Page, Traffic]]:
    """A page whose requests are answered from PAGES, so ATS hosts look real offline."""
    traffic = Traffic()

    async def serve(route: Route) -> None:
        request = route.request
        traffic.requests.append(f"{request.method} {request.url}")
        if request.method != "GET":
            await route.fulfill(body="<h1>Thanks for applying</h1>", content_type="text/html")
            return
        name = PAGES.get(request.url)
        if name is None:
            await route.fulfill(status=404, body="not found")
        else:
            await route.fulfill(path=PAGE_DIR / name, content_type="text/html")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        context = await browser.new_context()
        await context.route("**/*", serve)
        yield await context.new_page(), traffic
        await browser.close()


@dataclass
class ScriptedAnswerer:
    """Answers by question label, as a stated fact unless the label is in `reasoned`."""

    by_label: dict[str, str]
    reasoned: set[str] = field(default_factory=set)
    asked: list[AnswerInput] = field(default_factory=list)

    async def answer(self, request: AnswerInput) -> AnswerSet:
        self.asked.append(request)
        answers = [
            Answer(
                question_id=q.id,
                value=self.by_label[q.label],
                reasoned=q.label in self.reasoned,
                basis="a guess" if q.label in self.reasoned else "CV",
            )
            for q in request.questions
            if q.label in self.by_label
        ]
        return AnswerSet(answers=answers, concerns=[])


@dataclass
class ScriptedFinder:
    """Picks the control with the given text, or none; records what it was shown."""

    pick: str | None
    asked: list[tuple[FormFinderInput, bytes]] = field(default_factory=list)

    async def find(self, request: FormFinderInput, screenshot: bytes) -> FormFinding:
        self.asked.append((request, screenshot))
        chosen = next((c.id for c in request.controls if c.text == self.pick), None)
        return FormFinding(control_id=chosen, reason="scripted", concerns=[])


def agents(
    answerer: ScriptedAnswerer | None = None, finder: ScriptedFinder | None = None
) -> ApplyAgents:
    return ApplyAgents(answerer or ScriptedAnswerer({}), finder or ScriptedFinder(None))
