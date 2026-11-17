"""Every question the profile can't fill: an agent answers it, Python checks it before filling."""

import logging
import re
from dataclasses import dataclass
from typing import Protocol

from mozg_outreach.apply.models import Answer, AnswerInput, AnswerSet, FormField, Question
from mozg_outreach.pipelines.steps import PipelineSteps

logger = logging.getLogger(__name__)

CHOICE_KINDS = frozenset({"select", "radio"})
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
NUMBER = re.compile(r"-?\d+(\.\d+)?")


class Answerer(Protocol):
    async def answer(self, request: AnswerInput) -> AnswerSet: ...


class AgentAnswerer:
    def __init__(self, steps: PipelineSteps) -> None:
        self._steps = steps

    async def answer(self, request: AnswerInput) -> AnswerSet:
        return await self._steps.run("seeker", "application-answerer", request, AnswerSet)


def question(form_field: FormField) -> Question:
    return Question(
        id=form_field.id,
        kind=form_field.kind,
        label=form_field.label,
        required=form_field.required,
        options=[o.label for o in form_field.options],
        max_length=form_field.max_length,
    )


@dataclass(frozen=True)
class Checked:
    value: str | None
    problem: str | None = None


def check(form_field: FormField, answer: Answer) -> Checked:
    """The value to fill, or why the answer can't go in as it is."""
    value = answer.value.strip()
    if not value:
        return Checked(None, "empty answer")
    if form_field.kind in CHOICE_KINDS:
        for option in form_field.options:
            if value in (option.label, option.value):
                return Checked(option.value)
        return Checked(None, f"{value!r} is not one of the options")
    if form_field.kind == "checkboxes":
        picked = [line.strip() for line in value.split("\n") if line.strip()]
        by_text = {o.label: o.value for o in form_field.options} | {
            o.value: o.value for o in form_field.options
        }
        unknown = [p for p in picked if p not in by_text]
        if unknown:
            return Checked(None, f"{unknown[0]!r} is not one of the options")
        return Checked("\n".join(by_text[p] for p in picked))
    if form_field.kind == "checkbox":
        if value not in ("yes", "no"):
            return Checked(None, f"a checkbox takes yes or no, not {value!r}")
        return Checked(value)
    if form_field.kind == "date" and not ISO_DATE.fullmatch(value):
        return Checked(None, f"a date field takes YYYY-MM-DD, not {value!r}")
    if form_field.kind == "number" and not NUMBER.fullmatch(value):
        return Checked(None, f"a number field takes digits, not {value!r}")
    if form_field.max_length and len(value) > form_field.max_length:
        return Checked(None, f"longer than the field's {form_field.max_length} characters")
    return Checked(value)
