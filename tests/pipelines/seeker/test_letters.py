from pathlib import Path

import pytest

from mozg_outreach.agents import PromptLibrary
from mozg_outreach.letters.composer import WrittenLetter
from mozg_outreach.letters.models import LetterBrief, LetterDraft, SenderSettings
from mozg_outreach.naturalize.models import NaturalizeResult
from mozg_outreach.naturalize.naturalizer import Naturalizer
from mozg_outreach.pipelines.seeker.letters import CoverLetterRequest, JobLetterWriter
from mozg_outreach.pipelines.seeker.models import JobContacts, NamedContact, SeekerStrategy
from mozg_outreach.pipelines.seeker.skills import SkillMatcher
from mozg_outreach.pipelines.steps import PipelineSteps

from ..conftest import REPO_PROMPTS, RecordedRunner
from .conftest import CV_TEXT, judgment, listing

# Mentions only the listing's stack and facts the CV states; about 150 words.
BODY = (
    "Your team runs Python and PostgreSQL services on Kubernetes, and the listing says the "
    "backend carries most of the product. That is the kind of system I have spent my recent "
    "years on as a senior backend engineer in Berlin.\n\n"
    "I build and run Python services against PostgreSQL, and I deploy them on Kubernetes "
    "myself rather than handing them over to a separate platform team. I also write Go when a "
    "service needs it, so a mixed codebase would not slow me down. I care about boring "
    "releases, clear ownership and dashboards that answer the question before anyone asks "
    "it.\n\n"
    "What I would bring to Acme is a steady pair of hands for the services you already have "
    "and the habit of leaving them easier to change than I found them. I would be glad to "
    "talk about the role and how your backend team works today."
)


def contacts(email: str | None) -> JobContacts:
    return JobContacts(
        named_contact=NamedContact(
            name="Jane Example",
            title="Engineering Manager",
            linkedin=None,
            email=email,
            source_url="https://jobs.acme.example/team",
        ),
        careers_email=None,
        careers_email_source=None,
        linkedin_company=None,
        pattern_guesses=["careers@acme.example"],
        apply_url="https://jobs.acme.example/backend/apply",
        concerns=[],
    )


async def write(
    strategy: SeekerStrategy, found: JobContacts | None, tmp_path: Path
) -> tuple[WrittenLetter, RecordedRunner]:
    reader = found.named_contact.name if found and found.named_contact else "Hiring Team"
    framed = f"Dear {reader},\n\n{BODY}\n\nThanks,\nStan"
    draft = LetterDraft(
        subject=None, body=BODY, hook_used="listing", angle_used="role fit", concerns=[]
    )
    runner = RecordedRunner(
        {
            "expert-copywriter": [draft],
            "expert-naturalizer": [
                NaturalizeResult(text=framed, changes=[], specificity_needed=[], concerns=[])
            ],
        }
    )
    steps = PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path)
    item = listing()
    letter = await JobLetterWriter(steps, Naturalizer(steps)).write(
        CoverLetterRequest(
            listing=item,
            judgment=judgment(),
            contacts=found,
            skills=SkillMatcher(strategy).match(item.summary),
            background=CV_TEXT,
            sender=SenderSettings(name="Stan Petrov", tone="warm"),
        )
    )
    assert isinstance(letter, WrittenLetter)
    return letter, runner


async def test_no_named_contact_goes_to_hiring_team_via_apply_form(
    strategy: SeekerStrategy, tmp_path: Path
) -> None:
    letter, runner = await write(strategy, None, tmp_path)
    lines = letter.text.splitlines()
    assert (lines[0], lines[-2], lines[-1]) == ("Dear Hiring Team,", "Thanks,", "Stan")
    assert letter.subject is None
    assert letter.review.problems == []
    assert (letter.review.address, letter.review.address_confidence) == (
        "https://jobs.acme.example/backend",
        "apply_form",
    )
    [brief] = runner.payloads["expert-copywriter"]
    assert isinstance(brief, LetterBrief)
    assert brief.sender_background == CV_TEXT
    assert brief.subject_max_chars is None


class TestDelivery:
    async def test_a_published_email_wins_and_gets_an_application_subject(
        self, strategy: SeekerStrategy, tmp_path: Path
    ) -> None:
        letter, _ = await write(strategy, contacts("jane@acme.example"), tmp_path)
        assert letter.text.startswith("Dear Jane Example,")
        assert (letter.review.address, letter.review.address_confidence) == (
            "jane@acme.example",
            "published",
        )
        assert letter.subject == "Application: Senior Backend Engineer - Stan Petrov"

    @pytest.mark.parametrize(
        ("update", "address", "confidence"),
        [
            ({"careers_email": "hr@acme.example"}, "hr@acme.example", "published"),
            ({"company_email": "info@acme.example"}, "info@acme.example", "shared_inbox"),
            ({}, "careers@acme.example", "guessed"),
            ({"pattern_guesses": []}, "https://jobs.acme.example/backend/apply", "apply_form"),
        ],
        ids=["HR inbox", "company inbox", "guessed careers@", "application form"],
    )
    async def test_without_a_personal_email_the_next_route_is_used(
        self,
        strategy: SeekerStrategy,
        tmp_path: Path,
        update: dict[str, object],
        address: str,
        confidence: str,
    ) -> None:
        letter, _ = await write(strategy, contacts(None).model_copy(update=update), tmp_path)
        assert (letter.review.address, letter.review.address_confidence) == (address, confidence)
        assert (letter.subject is None) == (confidence == "apply_form")


RU_BODY = " ".join(
    [
        "В вакансии написано, что команда пишет сервисы на Python и PostgreSQL и сама выкатывает",
        "их в Kubernetes. Последние годы я занимаюсь ровно этим: проектирую, пишу и поддерживаю",
        "бэкенд, который держит основную нагрузку продукта.\n\nМне важно, чтобы релизы проходили",
        "спокойно, у каждого сервиса был понятный владелец, а графики отвечали на вопрос раньше,",
        "чем его кто-то задаст. Я сам настраиваю деплой и мониторинг и не передаю сервисы",
        "отдельной команде платформы. Когда сервису нужен Go, пишу и на нем, так что смешанная",
        "кодовая база меня не замедлит.\n\nВ вашу команду я принесу спокойную и аккуратную работу",
        "с теми сервисами, которые у вас уже есть, и привычку оставлять код проще, чем он был до",
        "меня. Буду рад обсудить роль, задачи на ближайшие месяцы и то, как сейчас устроена",
        "работа бэкенд команды. Удобно созвониться в любой день на этой или следующей неделе,",
        "напишите, пожалуйста, когда вам подходит, и я подстроюсь под ваше расписание.",
    ]
)


async def test_a_russian_posting_gets_a_russian_letter(
    strategy: SeekerStrategy, tmp_path: Path
) -> None:
    framed = f"Здравствуйте!\n\n{RU_BODY}\n\nСпасибо,\nStan"
    draft = LetterDraft(
        subject=None, body=RU_BODY, hook_used="listing", angle_used="role fit", concerns=[]
    )
    natural = NaturalizeResult(text=framed, changes=[], specificity_needed=[], concerns=[])
    runner = RecordedRunner({"expert-copywriter": [draft], "expert-naturalizer": [natural]})
    steps = PipelineSteps(runner, PromptLibrary(REPO_PROMPTS), tmp_path)
    item = listing(
        title="Senior Python разработчик",
        summary="Ищем бэкенд разработчика: Python, PostgreSQL, Kubernetes. Удаленно.",
    )
    letter = await JobLetterWriter(steps, Naturalizer(steps)).write(
        CoverLetterRequest(
            listing=item,
            judgment=judgment(),
            contacts=None,
            skills=SkillMatcher(strategy).match(item.summary),
            background=CV_TEXT,
            sender=SenderSettings(name="Stan Petrov", tone="warm"),
        )
    )

    assert isinstance(letter, WrittenLetter)
    [brief] = runner.payloads["expert-copywriter"]
    assert isinstance(brief, LetterBrief)
    assert brief.language == "ru"
    [naturalize] = runner.payloads["expert-naturalizer"]
    assert naturalize.model_dump()["language"] == "ru"
    assert letter.text == framed
    assert letter.review.problems == []
