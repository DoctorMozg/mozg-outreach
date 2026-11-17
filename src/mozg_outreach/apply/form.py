"""Reads an application form and fills it without ever submitting it."""

import asyncio
import json
import logging
from collections.abc import Awaitable
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Frame, Locator, Page, Route
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from pydantic import TypeAdapter

from mozg_outreach.apply.ats import GENERIC, Ats, ats_for
from mozg_outreach.apply.models import FormField, PageControl

logger = logging.getLogger(__name__)

MARK = "data-mozg-field"
SCAN_SCRIPT = (Path(__file__).parent / "scan.js").read_text(encoding="utf-8")
GUARD_SCRIPT = (Path(__file__).parent / "guard.js").read_text(encoding="utf-8")
APPLY_LINK_SCRIPT = (Path(__file__).parent / "apply_link.js").read_text(encoding="utf-8")
CONTROLS_SCRIPT = (Path(__file__).parent / "controls.js").read_text(encoding="utf-8")
CONTROL_MARK = "data-mozg-control"
TICK_SCRIPT = "(el, on) => { if (el.checked !== on) el.click(); return el.checked; }"
_FIELDS = TypeAdapter(list[FormField])
_CONTROLS = TypeAdapter(list[PageControl])
# A field that isn't editable within this is left to the applicant instead of stalling the fill.
ACTION_MS = 5_000
SCRIPT_SECONDS = 10.0
# A tab renders its form, or a button starts its navigation, within this.
PRESS_SETTLE_MS = 1_500
FORM_POLL_SECONDS = 0.5


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
        raw = await asyncio.wait_for(frame.evaluate(SCAN_SCRIPT, MARK), SCRIPT_SECONDS)
    except TimeoutError as exc:
        raise FormError(
            f"the page at {frame.url} did not answer within {SCRIPT_SECONDS:g} s"
        ) from exc
    except PlaywrightError as exc:
        raise FormError(f"could not read the form at {frame.url}: {exc}") from exc
    return _FIELDS.validate_python(raw)


async def has_form(page: Page) -> bool:
    """Whether the page shows a field to type into; a lone upload button is not a form."""
    _, frame = await locate_form(page)
    return any(f.kind != "file" for f in await scan(frame))


async def wait_for_form(page: Page, seconds: float) -> bool:
    """Whether a form shows within `seconds`; single-page apps fetch theirs after the load event."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while not await has_form(page):
        if loop.time() >= deadline:
            logger.info("no form at %s after %g s", page.url, seconds)
            return False
        await asyncio.sleep(FORM_POLL_SECONDS)
    return True


async def apply_link(page: Page) -> str | None:
    try:
        found = await asyncio.wait_for(page.evaluate(APPLY_LINK_SCRIPT), SCRIPT_SECONDS)
    except (TimeoutError, PlaywrightError) as exc:
        logger.info("no apply link read from %s: %s", page.url, exc)
        return None
    return found if isinstance(found, str) else None


async def page_controls(page: Page) -> list[PageControl]:
    try:
        raw = await asyncio.wait_for(page.evaluate(CONTROLS_SCRIPT, CONTROL_MARK), SCRIPT_SECONDS)
    except TimeoutError as exc:
        raise FormError(
            f"the page at {page.url} did not answer within {SCRIPT_SECONDS:g} s"
        ) from exc
    except PlaywrightError as exc:
        raise FormError(f"could not list the controls at {page.url}: {exc}") from exc
    return _CONTROLS.validate_python(raw)


async def press(page: Page, control: PageControl) -> None:
    """Clicks a tab or button outside every form; nothing the page sends meanwhile gets out."""
    target = page.locator(f'[{CONTROL_MARK}="{control.id}"]').first

    async def reads_only(route: Route) -> None:
        # A button outside a form can still post in the background, as one-click applies do;
        # GraphQL queries only read, and single-page apps fetch their form with one.
        if route.request.method != "GET" and not _graphql_query(route.request.post_data):
            logger.warning(
                "blocked %s %s after pressing %r",
                route.request.method,
                route.request.url,
                control.text,
            )
            await route.abort("blockedbyclient")
            return
        await route.fallback()

    await page.route("**/*", reads_only)
    try:
        await target.click(timeout=ACTION_MS)
        await page.wait_for_timeout(PRESS_SETTLE_MS)
    except PlaywrightError as exc:
        raise FormError(f"could not press {control.text!r}: {str(exc).splitlines()[0]}") from exc
    finally:
        await page.unroute("**/*", reads_only)
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=ACTION_MS)
    except PlaywrightTimeoutError:
        logger.info("%s still loading after pressing %r", page.url, control.text)


class SafeActor:
    """Types, picks, ticks and attaches; it never clicks a button or presses a key, so it can't
    send the form."""

    def __init__(self, frame: Frame) -> None:
        self._frame = frame

    async def fill(self, field: FormField, value: str) -> None:
        target = self._locate(field)
        match field.kind:
            case "select":
                await self._do(target.select_option(value=value, timeout=ACTION_MS), field)
            case "radio":
                await self._tick(self._option(field, value), True, field)
            case "checkbox":
                await self._tick(target, value == "yes", field)
            case "checkboxes":
                chosen = set(value.split("\n"))
                for option in field.options:
                    await self._tick(
                        self._option(field, option.value), option.value in chosen, field
                    )
            case "file":
                raise FormError(f"{field.label!r} takes a file; use attach")
            case "combobox":
                raise FormError(f"{field.label!r} is a custom dropdown the applicant picks")
            case _:
                text = value[: field.max_length] if field.max_length else value
                await self._do(target.fill(text, timeout=ACTION_MS), field)

    async def attach(self, field: FormField, path: Path) -> None:
        if field.kind != "file":
            raise FormError(f"{field.label!r} is not a file field")
        await self._do(self._locate(field).set_input_files(path, timeout=ACTION_MS), field)

    def _locate(self, field: FormField) -> Locator:
        return self._frame.locator(f'[{MARK}="{field.id}"]').first

    def _option(self, field: FormField, value: str) -> Locator:
        return self._frame.locator(f'[{MARK}="{field.id}"][value="{_css(value)}"]').first

    async def _tick(self, box: Locator, on: bool, field: FormField) -> None:
        # Styled forms hide the input under its label, where a pointer click never lands; a DOM
        # click toggles it and runs the page's handlers, and a checkbox or radio can't send a form.
        state = await self._do(box.evaluate(TICK_SCRIPT, on, timeout=ACTION_MS), field)
        if state is not on:
            raise FormError(f"{field.label!r} did not stay {'ticked' if on else 'clear'}")

    async def _do[T](self, action: Awaitable[T], field: FormField) -> T:
        try:
            return await action
        except PlaywrightTimeoutError as exc:
            raise FormError(
                f"{field.label!r} could not be filled within {ACTION_MS / 1000:g} s"
            ) from exc
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
            return int(
                await asyncio.wait_for(self._frame.evaluate(GUARD_SCRIPT, on), SCRIPT_SECONDS)
            )
        except TimeoutError as exc:
            raise FormError(
                f"the page at {self._frame.url} did not answer within {SCRIPT_SECONDS:g} s"
            ) from exc
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


def _graphql_query(body: str | None) -> bool:
    """Whether a request body is GraphQL that only reads: queries, no mutations."""
    try:
        payload = json.loads(body or "")
    except json.JSONDecodeError:
        return False
    operations = payload if isinstance(payload, list) else [payload]
    texts = [op.get("query") if isinstance(op, dict) else None for op in operations]
    return bool(texts) and all(
        isinstance(t, str) and t.lstrip().startswith(("query", "{")) for t in texts
    )


def _css(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
