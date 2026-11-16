"""Questions only the applicant answers: the service never fills them, the AI never sees them."""

import re

from mozg_outreach.apply.models import FormField

# Each pattern is matched against the label and the field name, case-folded.
PROTECTED: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "equal-opportunity and demographic questions are voluntary and personal",
        re.compile(
            r"\b(gender|sex|race|racial|ethnic\w*|hispanic|latin[oax]|veteran|disabilit\w*|"
            r"sexual orientation|transgender|pronouns?|date of birth|age|religio\w*|eeo\w*|"
            r"demographic\w*|marital)\b"
        ),
    ),
    (
        "pay expectations are the applicant's call",
        re.compile(
            r"\b(salary|compensation|(expected|desired) pay|pay (expectations?|range)|wages?|"
            r"(hourly|daily|day) rate|rate expectations?|remuneration|ctc)\b"
        ),
    ),
    (
        "work authorization and visa answers are legal statements",
        re.compile(
            r"\b(visa|sponsor\w*|work authori[sz]ation|authori[sz]ed to work|right to work|"
            r"work permit|citizenship|citizen|immigration|residency status)\b"
        ),
    ),
    (
        "consents, attestations and signatures need the applicant's own agreement",
        re.compile(
            r"\b(consent\w*|agree\w*|acknowledg\w*|attest\w*|certif(y|ies)|signature|"
            r"sign here|privacy (policy|notice)|terms|accurate and complete|non-?compete|"
            r"background check|criminal|convicted|record(ing|ed)? consent|receive (emails|texts|"
            r"messages|updates|job alerts)|marketing)\b"
        ),
    ),
    ("CAPTCHAs are for the applicant", re.compile(r"\b(captcha|recaptcha|hcaptcha|not a robot)\b")),
)


def protected_reason(field: FormField) -> str | None:
    text = f"{field.label} {field.name or ''}".casefold().replace("_", " ")
    for reason, pattern in PROTECTED:
        if pattern.search(text):
            return reason
    return None
