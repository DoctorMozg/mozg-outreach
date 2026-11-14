from pathlib import Path

from mozg_outreach.agents import PromptLibrary
from mozg_outreach.letters.composer import WrittenLetter
from mozg_outreach.letters.models import LetterBrief, LetterDraft, SenderSettings
from mozg_outreach.naturalize.models import NaturalizeResult
from mozg_outreach.naturalize.naturalizer import Naturalizer
from mozg_outreach.pipelines.job.letters import CoverLetterRequest, JobLetterWriter
from mozg_outreach.pipelines.job.models import JobContacts, JobStrategy, NamedContact
from mozg_outreach.pipelines.job.skills import SkillMatcher
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
    strategy: JobStrategy, found: JobContacts | None, tmp_path: Path
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
            cv_text=CV_TEXT,
            sender=SenderSettings(name="Stan Petrov", tone="warm"),
        )
    )
    assert isinstance(letter, WrittenLetter)
    return letter, runner


async def test_no_named_contact_goes_to_hiring_team_via_apply_form(
    strategy: JobStrategy, tmp_path: Path
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


async def test_named_contact_email_is_used_and_guesses_are_not(
    strategy: JobStrategy, tmp_path: Path
) -> None:
    letter, _ = await write(strategy, contacts("jane@acme.example"), tmp_path)
    assert letter.text.startswith("Dear Jane Example,")
    assert (letter.review.address, letter.review.address_confidence) == (
        "jane@acme.example",
        "published",
    )
    no_email, _ = await write(strategy, contacts(None), tmp_path)
    assert no_email.review.address == "https://jobs.acme.example/backend/apply"
