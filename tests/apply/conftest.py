from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from playwright.async_api import Page, Route, async_playwright

from mozg_outreach.apply.filler import Application
from mozg_outreach.apply.models import AnswerInput, AnswerSet, ApplicantProfile

EMBED = "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=1"
PAGES = {
    "https://careers.acme.example/jobs/1": "greenhouse_page.html",
    EMBED: "greenhouse_form.html",
    "https://jobs.lever.co/acme/1234/apply": "lever_form.html",
    "https://acme.wd3.myworkdayjobs.com/en-US/careers/job/1": "workday.html",
    "https://careers.acme.example/apply": "autosubmit_form.html",
    "https://careers.acme.example/jobs/2": "listing_page.html",
}
PAGE_DIR = Path(__file__).parent / "pages"

PROFILE = ApplicantProfile(
    first_name="Max",
    last_name="Example",
    email="max@example.com",
    phone="+44 20 7946 0000",
    linkedin="https://www.linkedin.com/in/max-example",
)


def application(letter: str | None = "Dear Acme team, ...") -> Application:
    return Application(
        profile=PROFILE,
        job_title="Data Engineer",
        company="Acme",
        job_summary="Real-time pricing pipelines in Python.",
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
    """Answers by question label; records what it was asked."""

    by_label: dict[str, str]
    asked: list[AnswerInput] = field(default_factory=list)

    async def answer(self, request: AnswerInput) -> AnswerSet:
        self.asked.append(request)
        answers = [
            {"question_id": q.id, "value": self.by_label[q.label], "basis": "CV"}
            for q in request.questions
            if q.label in self.by_label
        ]
        skipped = [q.id for q in request.questions if q.label not in self.by_label]
        return AnswerSet.model_validate({"answers": answers, "skipped": skipped, "concerns": []})
