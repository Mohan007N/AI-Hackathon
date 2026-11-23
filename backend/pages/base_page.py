"""
Base Page Object for Playwright async automation.

All domain-specific page objects inherit from BasePage.
Provides semantic Playwright locators:
- get_by_role()
- get_by_label()
- get_by_text()
- get_by_placeholder()
- locator()
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional
from playwright.async_api import Locator, Page, Response

logger = logging.getLogger(__name__)


class BasePage:
    """Base class for all Page Objects using Playwright Python."""

    def __init__(self, page: Page):
        if page is None:
            raise ValueError("BasePage requires a valid Playwright Page instance.")
        self.page: Page = page

    # ── Locators ─────────────────────────────────────────────────────────────

    def get_by_role(self, role: str, **kwargs) -> Locator:
        """Locate element by ARIA role and optional attributes (name, exact, etc.)."""
        return self.page.get_by_role(role, **kwargs)

    def get_by_label(self, text: str | re.Pattern, **kwargs) -> Locator:
        """Locate an input control by associated <label> text or aria-labelledby."""
        return self.page.get_by_label(text, **kwargs)

    def get_by_text(self, text: str | re.Pattern, **kwargs) -> Locator:
        """Locate element by visible text content."""
        return self.page.get_by_text(text, **kwargs)

    def get_by_placeholder(self, text: str | re.Pattern, **kwargs) -> Locator:
        """Locate input element by placeholder attribute."""
        return self.page.get_by_placeholder(text, **kwargs)

    def get_by_title(self, text: str | re.Pattern, **kwargs) -> Locator:
        """Locate element by title attribute."""
        return self.page.get_by_title(text, **kwargs)

    def locator(self, selector: str, **kwargs) -> Locator:
        """Locate elements using CSS, XPath, or Playwright engine selector."""
        return self.page.locator(selector, **kwargs)

    # ── Navigation & Lifecycle ────────────────────────────────────────────────

    async def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: Optional[float] = None) -> Optional[Response]:
        """Navigate to specified URL."""
        if not url.startswith(("http://", "https://", "data:", "file://")):
            url = "https://" + url
        return await self.page.goto(url, wait_until=wait_until, timeout=timeout)

    @property
    def url(self) -> str:
        """Return the current URL of the page."""
        return self.page.url

    async def title(self) -> str:
        """Return the current title of the page."""
        return await self.page.title()

    async def wait_for_load_state(self, state: str = "domcontentloaded", timeout: Optional[float] = None) -> None:
        """Wait for page load state (load, domcontentloaded, networkidle)."""
        try:
            await self.page.wait_for_load_state(state, timeout=timeout)
        except Exception as exc:
            logger.debug("wait_for_load_state '%s' timed out or ignored: %s", state, exc)

    async def wait_for_url(self, url_or_pattern: str | re.Pattern, timeout: Optional[float] = None) -> None:
        """Wait until page URL matches the target pattern."""
        await self.page.wait_for_url(url_or_pattern, timeout=timeout)

    # ── Element State & Helpers ───────────────────────────────────────────────

    async def is_visible(self, locator: Locator) -> bool:
        """Check whether element is visible on page."""
        try:
            return await locator.is_visible()
        except Exception:
            return False

    async def get_text(self, locator: Locator) -> str:
        """Extract inner text of element trimmed."""
        try:
            return (await locator.inner_text()).strip()
        except Exception:
            return ""

    async def get_page_text(self) -> str:
        """Get visible body text of current page."""
        try:
            return await self.page.inner_text("body")
        except Exception:
            try:
                return await self.page.content()
            except Exception:
                return ""

    async def take_screenshot(self, path: Optional[str] = None, full_page: bool = False) -> bytes:
        """Capture screenshot as bytes and optionally save to path."""
        data = await self.page.screenshot(path=path, full_page=full_page)
        return data

