import base64

import pytest
from pydantic import ValidationError

from mozg_outreach.apply.answers import check
from mozg_outreach.apply.ats import GENERIC, GREENHOUSE, LEVER, ats_for, form_url, standard_key
from mozg_outreach.apply.models import (
    PHOTO_BYTES,
    Answer,
    ApplicantProfile,
    FieldKind,
    FieldOption,
    FormField,
    decode_photo,
)
from mozg_outreach.apply.policy import protected_reason
from tests.apply.conftest import PHOTO


def form_field(label: str, kind: FieldKind = "text", name: str | None = None) -> FormField:
    return FormField(
        id="f1",
        kind=kind,
        label=label,
        name=name,
        required=False,
        options=[FieldOption(value="1", label="Yes"), FieldOption(value="2", label="No")],
        max_length=20,
    )


class TestProtected:
    @pytest.mark.parametrize(
        "label",
        [
            "Gender",
            "Are you a protected veteran?",
            "Pronouns",
            "What are your salary expectations?",
            "Desired pay",
            "Will you now or in the future require sponsorship?",
            "Do you have the right to work in Germany?",
            "I agree to the privacy policy",
            "I certify that the information is accurate and complete",
            "Signature",
            "Send me job alerts: receive emails about new roles",
        ],
    )
    def test_personal_and_legal_questions_are_protected(self, label: str) -> None:
        assert protected_reason(form_field(label))

    @pytest.mark.parametrize(
        "label",
        [
            "Why do you want to join us?",
            "Rate your experience with Kafka",
            "Which languages do you speak?",
            "List your AWS certifications",
            "Link to a page you built",
        ],
    )
    def test_ordinary_questions_are_not(self, label: str) -> None:
        assert protected_reason(form_field(label)) is None

    def test_the_field_name_counts_too(self) -> None:
        assert protected_reason(form_field("Question 7", name="eeo_race"))


LEVER_JOB = "https://jobs.lever.co/acme/5f6c0a2e-1b2c-4d3e-9f00-123456789abc"


@pytest.mark.parametrize(
    ("job_page", "form"),
    [
        (
            "https://crossing.recruitee.com/o/backend-developer?lang=en",
            "https://crossing.recruitee.com/o/backend-developer/c/new?lang=en",
        ),
        (LEVER_JOB, f"{LEVER_JOB}/apply"),
        (
            "https://jobs.ashbyhq.com/acme/5f6c0a2e-1b2c-4d3e-9f00-123456789abc",
            "https://jobs.ashbyhq.com/acme/5f6c0a2e-1b2c-4d3e-9f00-123456789abc/application",
        ),
    ],
    ids=["recruitee", "lever", "ashby"],
)
def test_a_known_systems_job_page_leads_to_its_form(job_page: str, form: str) -> None:
    assert form_url(job_page) == form
    assert form_url(form) == form, "the form page itself stays"


def test_other_pages_are_opened_as_they_are() -> None:
    for url in ("https://acme.example/careers/1", "https://job-boards.greenhouse.io/acme/jobs/1"):
        assert form_url(url) == url


class TestStandardFields:
    def test_systems_are_found_by_host(self) -> None:
        assert ats_for("https://job-boards.greenhouse.io/acme/jobs/1") is GREENHOUSE
        assert ats_for("https://jobs.lever.co/acme/1/apply") is LEVER
        assert ats_for("https://notgreenhouse.io/x") is GENERIC

    @pytest.mark.parametrize(
        ("label", "kind", "key"),
        [
            ("First Name *", "text", "first_name"),
            ("Surname", "text", "last_name"),
            ("Name", "text", "full_name"),
            ("Company name", "text", None),
            ("Resume/CV", "file", "resume"),
            ("Cover letter", "textarea", "cover_letter"),
            ("Resume", "textarea", None),
            ("Where are you based?", "text", "location"),
            ("Preferred first name", "text", "preferred_name"),
            ("Email address", "text", "email"),
            ("Address", "text", "address"),
            ("City", "text", "city"),
            ("ZIP code", "text", "postal_code"),
            ("Country", "select", "country"),
            ("Current job title", "text", "current_title"),
            ("Years of experience *", "text", "years_experience"),
            ("Years of Python", "select", None),
            ("Which languages do you speak?", "text", "languages"),
            ("University", "text", "university"),
            ("Highest degree", "select", "degree"),
            ("Notice period", "text", "notice_period"),
            ("When can you start?", "text", "available_from"),
            ("Photo", "file", "photo"),
            ("Profile picture", "file", "photo"),
        ],
    )
    def test_labels_map_to_profile_keys(self, label: str, kind: FieldKind, key: str | None) -> None:
        assert standard_key(GENERIC, form_field(label, kind)) == key

    def test_a_known_name_beats_the_label(self) -> None:
        assert standard_key(LEVER, form_field("Your org", name="org")) == "current_company"


class TestApplicantPhoto:
    def test_an_image_data_url_decodes_with_its_extension(self) -> None:
        photo = decode_photo(PHOTO)
        assert photo.extension == "png"
        assert photo.data.startswith(b"\x89PNG")

    @pytest.mark.parametrize(
        ("url", "problem"),
        [
            ("https://example.com/me.jpg", "base64 image data URL"),
            ("data:image/gif;base64,R0lGOD==", "JPEG, PNG or WebP"),
            (
                "data:image/png;base64," + base64.b64encode(bytes(PHOTO_BYTES + 1)).decode(),
                "byte limit",
            ),
        ],
        ids=["link", "gif", "too-big"],
    )
    def test_the_profile_rejects_other_photos(self, url: str, problem: str) -> None:
        with pytest.raises(ValidationError, match=problem):
            ApplicantProfile(first_name="Max", last_name="Example", email="m@x.io", photo=url)

    def test_values_read_as_text_and_files_have_none(self) -> None:
        profile = ApplicantProfile(
            first_name="Max", last_name="Example", email="m@x.io", years_experience=12, photo=PHOTO
        )
        assert profile.value("years_experience") == "12"
        assert profile.value("photo") is None


class TestAnswerCheck:
    def test_a_choice_maps_the_label_to_its_value(self) -> None:
        assert (
            check(form_field("Q", "select"), Answer(question_id="f1", value="No", basis="")).value
            == "2"
        )

    @pytest.mark.parametrize(
        ("kind", "value", "problem"),
        [
            ("radio", "Maybe", "not one of the options"),
            ("checkbox", "true", "takes yes or no"),
            ("text", "x" * 21, "longer than"),
            ("text", "  ", "empty"),
        ],
    )
    def test_bad_answers_are_refused(self, kind: FieldKind, value: str, problem: str) -> None:
        checked = check(form_field("Q", kind), Answer(question_id="f1", value=value, basis=""))
        assert checked.value is None
        assert problem in (checked.problem or "")
