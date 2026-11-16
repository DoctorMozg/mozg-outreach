"""Applicant tracking systems: where the form lives and which fields are standard."""

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from mozg_outreach.apply.models import FormField, ProfileKey


@dataclass(frozen=True)
class Ats:
    name: str
    hosts: tuple[str, ...] = ()
    # Field `name` attributes this system uses for standard fields; labels cover the rest.
    names: dict[str, ProfileKey] = field(default_factory=dict)
    # Account-per-employer systems: the form opens for the applicant, nothing is filled.
    manual: bool = False

    def matches(self, url: str) -> bool:
        host = urlsplit(url).hostname or ""
        return any(host == h or host.endswith(f".{h}") for h in self.hosts)


GREENHOUSE = Ats(
    name="greenhouse",
    hosts=("greenhouse.io",),
    names={
        "first_name": "first_name",
        "last_name": "last_name",
        "email": "email",
        "phone": "phone",
        "resume": "resume",
        "cover_letter": "cover_letter",
    },
)
LEVER = Ats(
    name="lever",
    hosts=("lever.co",),
    names={
        "name": "full_name",
        "email": "email",
        "phone": "phone",
        "location": "location",
        "org": "current_company",
        "urls[LinkedIn]": "linkedin",
        "urls[GitHub]": "github",
        "urls[Portfolio]": "website",
        "urls[Other]": "website",
        "resume": "resume",
    },
)
ASHBY = Ats(
    name="ashby",
    hosts=("ashbyhq.com",),
    names={
        "_systemfield_name": "full_name",
        "_systemfield_email": "email",
        "_systemfield_resume": "resume",
    },
)
WORKDAY = Ats(name="workday", hosts=("myworkdayjobs.com", "workday.com"), manual=True)
GENERIC = Ats(name="generic")
SYSTEMS = (GREENHOUSE, LEVER, ASHBY, WORKDAY)

# Label patterns for standard fields, tried in order on the label without its asterisk.
LABELS: tuple[tuple[ProfileKey, re.Pattern[str]], ...] = (
    ("first_name", re.compile(r"\b(first|given) name\b")),
    ("last_name", re.compile(r"\b(last|family) name\b|\bsurname\b")),
    ("full_name", re.compile(r"^(full name|name|your name|legal name)$")),
    ("email", re.compile(r"\be-?mail\b")),
    ("phone", re.compile(r"\b(phone|mobile|telephone)\b")),
    ("linkedin", re.compile(r"\blinkedin\b")),
    ("github", re.compile(r"\bgithub\b")),
    ("website", re.compile(r"\b(website|portfolio|personal site)\b")),
    ("current_company", re.compile(r"^(current (company|employer)|company)$")),
    ("location", re.compile(r"^(current )?(location|city)\b|\bwhere are you (based|located)\b")),
)
FILES: tuple[tuple[ProfileKey, re.Pattern[str]], ...] = (
    ("cover_letter", re.compile(r"\bcover letter\b")),
    ("resume", re.compile(r"\b(resume|résumé|cv)\b")),
)


def ats_for(url: str) -> Ats:
    return next((ats for ats in SYSTEMS if ats.matches(url)), GENERIC)


def standard_key(ats: Ats, form_field: FormField) -> ProfileKey | None:
    if form_field.name and form_field.name in ats.names:
        return ats.names[form_field.name]
    label = form_field.label.casefold().replace("*", "").strip()
    if form_field.kind == "file":
        patterns = FILES
    elif form_field.kind == "textarea":
        patterns = FILES[:1]  # a pasted cover letter; other long answers go to the answerer
    else:
        patterns = LABELS
    return next((key for key, pattern in patterns if pattern.search(label)), None)
