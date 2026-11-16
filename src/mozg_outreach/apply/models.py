from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FieldKind = Literal[
    "text",
    "email",
    "tel",
    "url",
    "number",
    "date",
    "textarea",
    "select",
    "radio",
    "checkbox",
    "file",
    "combobox",
]
ProfileKey = Literal[
    "first_name",
    "last_name",
    "full_name",
    "email",
    "phone",
    "location",
    "linkedin",
    "github",
    "website",
    "current_company",
    "resume",
    "cover_letter",
]


class FieldOption(BaseModel):
    value: str
    label: str


class FormField(BaseModel):
    """One question on an application form, found by the DOM scan."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="The data-mozg-field mark the scan put on the element.")
    kind: FieldKind
    label: str
    name: str | None
    required: bool
    options: list[FieldOption] = Field(default_factory=list)
    max_length: int | None = None


class ApplicantProfile(BaseModel):
    """What the user fills on every form; it never goes to an agent."""

    first_name: str
    last_name: str
    email: str
    phone: str | None = None
    location: str | None = None
    linkedin: str | None = Field(default=None, title="LinkedIn URL")
    github: str | None = Field(default=None, title="GitHub URL")
    website: str | None = Field(default=None, title="Website or portfolio")
    current_company: str | None = None

    def value(self, key: ProfileKey) -> str | None:
        if key == "full_name":
            return f"{self.first_name} {self.last_name}"
        if key in ("resume", "cover_letter"):
            return None
        values: dict[str, str | None] = self.model_dump()
        return values[key]


class Question(BaseModel):
    id: str
    kind: FieldKind
    label: str
    required: bool
    options: list[str] = Field(default_factory=list, description="Allowed answers, verbatim.")
    max_length: int | None = None


class AnswerInput(BaseModel):
    job_title: str
    company: str
    job_summary: str
    cv_text: str
    questions: list[Question]


class Answer(BaseModel):
    question_id: str
    value: str = Field(description="Text, or one option verbatim; checkboxes take yes or no.")
    basis: str = Field(description="The CV or posting fact the answer rests on.")


class AnswerSet(BaseModel):
    answers: list[Answer]
    skipped: list[str] = Field(description="Question ids left for the applicant.")
    concerns: list[str]


FillSource = Literal["profile", "cv", "letter", "answerer"]


class FilledField(BaseModel):
    label: str
    source: FillSource


class OpenField(BaseModel):
    label: str
    required: bool
    reason: str


class FillReport(BaseModel):
    ats: str
    url: str
    filled: list[FilledField]
    for_user: list[OpenField]
    concerns: list[str]
