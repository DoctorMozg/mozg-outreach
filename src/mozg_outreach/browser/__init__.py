"""A guarded headless browser for agents that need to read JavaScript-rendered pages."""

from mozg_outreach.browser.guard import UrlGuard, public_address
from mozg_outreach.browser.service import (
    BrowserError,
    BrowserService,
    BrowserSession,
    BrowserSettings,
    PageView,
)

__all__ = [
    "BrowserError",
    "BrowserService",
    "BrowserSession",
    "BrowserSettings",
    "PageView",
    "UrlGuard",
    "public_address",
]
