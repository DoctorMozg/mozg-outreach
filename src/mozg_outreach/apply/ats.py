"""Applicant tracking systems: where the form lives and which fields are standard."""

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

from mozg_outreach.apply.models import FormField, ProfileKey


@dataclass(frozen=True)
class Ats:
    name: str
    hosts: tuple[str, ...] = ()
    # Field `name` attributes this system uses for standard fields; labels cover the rest.
    names: dict[str, ProfileKey] = field(default_factory=dict)
    # Account-per-employer systems: the form opens for the applicant, nothing is filled.
    manual: bool = False
    # Turns a job page path into its application form path; the form is a separate page.
    form_path: tuple[re.Pattern[str], str] | None = None

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
    form_path=(re.compile(r"^(/[^/]+/[0-9a-f-]{8,})/?$"), r"\1/apply"),
)
ASHBY = Ats(
    name="ashby",
    hosts=("ashbyhq.com",),
    names={
        "_systemfield_name": "full_name",
        "_systemfield_email": "email",
        "_systemfield_resume": "resume",
    },
    form_path=(re.compile(r"^(/[^/]+/[0-9a-f-]{8,})/?$"), r"\1/application"),
)
RECRUITEE = Ats(
    name="recruitee",
    hosts=("recruitee.com",),
    names={
        "candidate.name": "full_name",
        "candidate.email": "email",
        "candidate.phone": "phone",
        "candidate.cv": "resume",
    },
    form_path=(re.compile(r"^(/o/[^/]+)/?$"), r"\1/c/new"),
)
WORKDAY = Ats(name="workday", hosts=("myworkdayjobs.com", "workday.com"), manual=True)
GENERIC = Ats(name="generic")
SYSTEMS = (GREENHOUSE, LEVER, ASHBY, RECRUITEE, WORKDAY)

# Label patterns for standard fields, tried in order on the label without its asterisk.
LABELS: tuple[tuple[ProfileKey, re.Pattern[str]], ...] = (
    # Before first_name, which would also match "preferred first name".
    ("preferred_name", re.compile(r"\bpreferred (first )?name\b")),
    ("first_name", re.compile(r"\b(first|given) name\b")),
    ("last_name", re.compile(r"\b(last|family) name\b|\bsurname\b")),
    ("full_name", re.compile(r"^(full name|name|your name|legal name)$")),
    ("email", re.compile(r"\be-?mail\b")),
    ("phone", re.compile(r"\b(phone|mobile|telephone)\b")),
    ("linkedin", re.compile(r"\blinkedin\b")),
    ("github", re.compile(r"\bgithub\b")),
    ("website", re.compile(r"\b(website|portfolio|personal site)\b")),
    ("current_company", re.compile(r"^(current (company|employer)|company)$")),
    ("current_title", re.compile(r"^(current )?(job )?title$|^current (role|position)$")),
    ("location", re.compile(r"^(current )?location\b|\bwhere are you (based|located)\b")),
    ("address", re.compile(r"^(street |home )?address( line 1)?$")),
    ("city", re.compile(r"^(current )?(city|town)( of residence)?$")),
    ("postal_code", re.compile(r"\b(zip|postal|post) ?code\b|^postcode$")),
    ("country", re.compile(r"^(current )?country( of residence)?$|^country/region$")),
    # Total experience only; years with one skill depend on the job, so the answerer takes them.
    (
        "years_experience",
        re.compile(
            r"^(total )?years of (professional |work )?experience$|"
            r"^how many years of (professional |work )?experience do you have\??$"
        ),
    ),
    ("languages", re.compile(r"^(spoken )?languages?( spoken)?$|\b(which|what) languages\b")),
    ("university", re.compile(r"^(school|university|college|institution)( name)?$")),
    ("degree", re.compile(r"^(highest )?(degree|level of education|education level)$")),
    ("notice_period", re.compile(r"\bnotice period\b")),
    (
        "available_from",
        re.compile(r"\b(earliest|possible) start date\b|^start date$|\bwhen can you start\b"),
    ),
)
FILES: tuple[tuple[ProfileKey, re.Pattern[str]], ...] = (
    ("cover_letter", re.compile(r"\bcover letter\b")),
    ("photo", re.compile(r"\b(photo|photograph|picture|headshot)\b")),
    ("resume", re.compile(r"\b(resume|résumé|cv)\b")),
)


def ats_for(url: str) -> Ats:
    return next((ats for ats in SYSTEMS if ats.matches(url)), GENERIC)


def form_url(url: str) -> str:
    """The application form's own page for a job page on a known system, else the URL as is."""
    rule = ats_for(url).form_path
    if rule is None:
        return url
    parts = urlsplit(url)
    pattern, replacement = rule
    return urlunsplit(parts._replace(path=pattern.sub(replacement, parts.path)))


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
