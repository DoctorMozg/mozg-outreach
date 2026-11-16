import pytest

from mozg_outreach.apply.answers import check
from mozg_outreach.apply.ats import GENERIC, GREENHOUSE, LEVER, ats_for, standard_key
from mozg_outreach.apply.models import Answer, FieldKind, FieldOption, FormField
from mozg_outreach.apply.policy import protected_reason


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
        ],
    )
    def test_labels_map_to_profile_keys(self, label: str, kind: FieldKind, key: str | None) -> None:
        assert standard_key(GENERIC, form_field(label, kind)) == key

    def test_a_known_name_beats_the_label(self) -> None:
        assert standard_key(LEVER, form_field("Your org", name="org")) == "current_company"


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
