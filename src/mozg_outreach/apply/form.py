"""Reads an application form and fills it without ever submitting it."""

import logging
from collections.abc import Awaitable
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Frame, Locator, Page, Route
from pydantic import TypeAdapter

from mozg_outreach.apply.ats import GENERIC, Ats, ats_for
from mozg_outreach.apply.models import FormField

logger = logging.getLogger(__name__)

MARK = "data-mozg-field"
SCAN_SCRIPT = (Path(__file__).parent / "scan.js").read_text(encoding="utf-8")
GUARD_SCRIPT = (Path(__file__).parent / "guard.js").read_text(encoding="utf-8")
_FIELDS = TypeAdapter(list[FormField])


class FormError(RuntimeError):
    """The form could not be read or a field could not be filled."""


async def locate_form(page: Page) -> tuple[Ats, Frame]:
    """The system and frame holding the form; employer pages often embed an ATS in an iframe."""
    ats = ats_for(page.url)
    if ats is GENERIC:
        for frame in page.frames[1:]:
            embedded = ats_for(frame.url)
            if embedded is not GENERIC:
                return embedded, frame
    return ats, page.main_frame


async def scan(frame: Frame) -> list[FormField]:
    try:
        raw = await frame.evaluate(SCAN_SCRIPT, MARK)
    except PlaywrightError as exc:
        raise FormError(f"could not read the form at {frame.url}: {exc}") from exc
    return _FIELDS.validate_python(raw)


class SafeActor:
    """Types, picks and attaches; it has no click or key verbs, so it can't send the form."""

    def __init__(self, frame: Frame) -> None:
        self._frame = frame

    async def fill(self, field: FormField, value: str) -> None:
        target = self._locate(field)
        match field.kind:
            case "select":
                await self._do(target.select_option(value=value), field)
            case "radio":
                choice = self._frame.locator(f'[{MARK}="{field.id}"][value="{_css(value)}"]')
                await self._do(choice.check(), field)
            case "checkbox":
                await self._do(target.set_checked(value == "yes"), field)
            case "file":
                raise FormError(f"{field.label!r} takes a file; use attach")
            case "combobox":
                raise FormError(f"{field.label!r} is a custom dropdown the applicant picks")
            case _:
                text = value[: field.max_length] if field.max_length else value
                await self._do(target.fill(text), field)

    async def attach(self, field: FormField, path: Path) -> None:
        if field.kind != "file":
            raise FormError(f"{field.label!r} is not a file field")
        await self._do(self._locate(field).set_input_files(path), field)

    def _locate(self, field: FormField) -> Locator:
        return self._frame.locator(f'[{MARK}="{field.id}"]').first

    async def _do(self, action: Awaitable[object], field: FormField) -> None:
        try:
            await action
        except PlaywrightError as exc:
            raise FormError(f"could not fill {field.label!r}: {str(exc).splitlines()[0]}") from exc


class SubmitGuard:
    """Stops the form from being sent while the service fills it.

    Pages submit from change handlers, and the navigation can start after a network guard is
    gone, so submission is switched off inside the frame; the route catches anything else.
    """

    def __init__(self, page: Page, frame: Frame) -> None:
        self._page = page
        self._frame = frame
        self.blocked: list[str] = []

    async def __aenter__(self) -> "SubmitGuard":
        await self._page.route("**/*", self._route)
        await self._switch(on=True)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        # The applicant submits by hand next, so both guards must be gone by then.
        stopped = await self._switch(on=False)
        if stopped:
            self.blocked.append(f"{stopped} submit attempt(s) by the page's own scripts")
        await self._page.unroute("**/*", self._route)

    async def _switch(self, *, on: bool) -> int:
        try:
            return int(await self._frame.evaluate(GUARD_SCRIPT, on))
        except PlaywrightError as exc:
            raise FormError(f"could not guard the form at {self._frame.url}: {exc}") from exc

    async def _route(self, route: Route) -> None:
        request = route.request
        if request.is_navigation_request() and request.method != "GET":
            self.blocked.append(f"{request.method} {request.url}")
            logger.warning("blocked a form submission to %s while filling", request.url)
            await route.abort("blockedbyclient")
            return
        await route.fallback()


def _css(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
