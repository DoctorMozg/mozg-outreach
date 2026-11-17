"""Questions only the applicant can answer; everything else is filled, and guesses are flagged."""

import re

from mozg_outreach.apply.models import FormField

CAPTCHA = re.compile(r"\b(captcha|recaptcha|hcaptcha|not a robot)\b")


def protected_reason(field: FormField) -> str | None:
    text = f"{field.label} {field.name or ''}".casefold().replace("_", " ")
    return "CAPTCHAs are for the applicant" if CAPTCHA.search(text) else None
