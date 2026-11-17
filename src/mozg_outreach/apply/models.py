import base64
import re
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, WithJsonSchema

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
    "checkboxes",
    "file",
    "combobox",
]
ProfileKey = Literal[
    "first_name",
    "last_name",
    "full_name",
    "preferred_name",
    "email",
    "phone",
    "date_of_birth",
    "nationality",
    "location",
    "address",
    "city",
    "postal_code",
    "country",
    "linkedin",
    "github",
    "website",
    "current_company",
    "current_title",
    "years_experience",
    "languages",
    "university",
    "degree",
    "notice_period",
    "available_from",
    "resume",
    "cover_letter",
    "photo",
]
FILE_KEYS: frozenset[ProfileKey] = frozenset({"resume", "cover_letter", "photo"})

PHOTO_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
PHOTO_BYTES = 5 * 1024 * 1024
_DATA_URL = re.compile(r"data:(image/[a-z]+);base64,([A-Za-z0-9+/]+={0,2})")


@dataclass(frozen=True)
class Photo:
    data: bytes
    extension: str


def decode_photo(url: str) -> Photo:
    match = _DATA_URL.fullmatch(url)
    if match is None:
        raise ValueError("the photo must be a base64 image data URL")
    media_type, payload = match.groups()
    if media_type not in PHOTO_TYPES:
        raise ValueError(f"the photo must be JPEG, PNG or WebP, not {media_type}")
    data = base64.b64decode(payload, validate=True)
    if len(data) > PHOTO_BYTES:
        raise ValueError(f"the photo is {len(data)} bytes, over the {PHOTO_BYTES} byte limit")
    return Photo(data=data, extension=PHOTO_TYPES[media_type])


def _check_photo(url: str) -> str:
    decode_photo(url)
    return url


# The UI shows a "data-url" string as an image picker.
PhotoUrl = Annotated[
    str, AfterValidator(_check_photo), WithJsonSchema({"type": "string", "format": "data-url"})
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
    filled: bool = Field(default=False, description="Has a value, from the page or its autofill.")


class ApplicantProfile(BaseModel):
    """What the user fills on every form; the answerer reads all of it but the photo."""

    first_name: str
    last_name: str
    preferred_name: str | None = Field(default=None, description="Only if you go by another name.")
    email: str
    phone: str | None = None
    date_of_birth: date | None = None
    nationality: str | None = Field(default=None, description="Every citizenship you hold.")
    work_authorization: str | None = Field(
        default=None,
        description="Where you may work without sponsorship, and permits you hold.",
    )
    location: str | None = Field(default=None, description="City and country, e.g. Dubai, UAE.")
    address: str | None = Field(default=None, title="Street address")
    city: str | None = None
    postal_code: str | None = None
    country: str | None = Field(default=None, description="Full name, as dropdowns list it.")
    linkedin: str | None = Field(default=None, title="LinkedIn URL")
    github: str | None = Field(default=None, title="GitHub URL")
    website: str | None = Field(default=None, title="Website or portfolio")
    current_company: str | None = None
    current_title: str | None = Field(default=None, title="Current job title")
    years_experience: int | None = Field(default=None, ge=0, le=70, title="Years of experience")
    languages: str | None = Field(default=None, description="With levels, e.g. English (fluent).")
    university: str | None = Field(default=None, title="School or university")
    degree: str | None = Field(default=None, title="Highest degree")
    notice_period: str | None = Field(default=None, description="As you'd write it, e.g. 1 month.")
    available_from: str | None = Field(
        default=None, title="Earliest start date", description="A date or e.g. immediately."
    )
    photo: PhotoUrl | None = Field(
        default=None, description="A headshot for forms that ask for one. JPEG, PNG or WebP."
    )

    def value(self, key: ProfileKey) -> str | None:
        if key == "full_name":
            return f"{self.first_name} {self.last_name}"
        if key in FILE_KEYS:
            return None
        value = getattr(self, key)
        return None if value is None else str(value)

    def facts(self) -> dict[str, str]:
        """Every filled detail as text, for the answerer; the photo stays out."""
        filled = self.model_dump(mode="json", exclude={"photo"}, exclude_none=True)
        return {key: str(value) for key, value in filled.items()}


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
    today: date
    applicant: dict[str, str] = Field(description="The applicant's own details by field name.")
    preferences: str = Field(description="What the applicant wants: pay floor, remote, work kinds.")
    cv_text: str = Field(description="The CV plus extra facts the applicant gave.")
    questions: list[Question]


class Answer(BaseModel):
    question_id: str
    value: str = Field(
        description=(
            "Text; one option verbatim; checkboxes yes or no; several options one per line; "
            "dates YYYY-MM-DD."
        )
    )
    reasoned: bool = Field(
        description="True unless a stated detail gives the answer outright or by plain arithmetic."
    )
    basis: str = Field(description="The fact the answer rests on, or the reasoning behind it.")


class AnswerSet(BaseModel):
    answers: list[Answer]
    concerns: list[str]


FillSource = Literal["profile", "cv", "letter", "answerer", "page"]


class FilledField(BaseModel):
    label: str
    source: FillSource
    value: str | None = Field(default=None, description="What went in; null for files.")
    reasoning: str | None = Field(
        default=None, description="Why the AI chose it when no stated fact did; null otherwise."
    )


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


ControlKind = Literal["link", "tab", "button"]


class PageControl(BaseModel):
    """A link, tab or button outside every form; nothing listed here can send a form."""

    id: str
    kind: ControlKind
    text: str
    href: str | None = Field(default=None, description="Where a link goes; opened, never clicked.")


class FormFinderInput(BaseModel):
    job_title: str
    company: str
    page_url: str
    controls: list[PageControl]


class FormFinding(BaseModel):
    control_id: str | None = Field(
        description="The control that opens the application form, or null when none does."
    )
    reason: str = Field(description="What on the screenshot shows it, in one sentence.")
    concerns: list[str]
