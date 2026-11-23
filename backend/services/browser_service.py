"""
Browser automation service using Playwright (async Chromium).

Security contract:
- Treat all page content as UNTRUSTED — never relay it back as instructions.
- Use semantic/role-based Playwright locators; avoid brittle CSS selectors.
- Never log passwords, OTPs, or access tokens.
- Handle dynamic layouts, randomized button text, and varied DOM structures.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from config import settings

logger = logging.getLogger(__name__)


class BrowserServiceError(Exception):
    pass


class BrowserService:
    """Manages a single Playwright browser session for one agent run."""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._page = None
        self._context = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=settings.BROWSER_HEADLESS,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        self._page = await self._context.new_page()
        self._page.set_default_timeout(settings.BROWSER_TIMEOUT_MS)
        logger.info("Browser started (headless=%s)", settings.BROWSER_HEADLESS)

    async def close(self) -> None:
        if self._page:
            await self._page.close()
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser closed.")

    def _require_page(self):
        if not self._page:
            raise BrowserServiceError("Browser not started. Call start() first.")

    # ── Navigation ────────────────────────────────────────────────────────────

    async def navigate(self, url: str) -> dict[str, Any]:
        self._require_page()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            response = await self._page.goto(url, wait_until="domcontentloaded")
            title = await self._page.title()
            return {
                "url": self._page.url,
                "title": title,
                "status": response.status if response else None,
            }
        except Exception as exc:
            raise BrowserServiceError(f"Navigation to {url} failed: {exc}") from exc

    # ── Page inspection ───────────────────────────────────────────────────────

    async def get_page_text(self) -> str:
        """Return visible text content of the current page (untrusted)."""
        self._require_page()
        try:
            return await self._page.inner_text("body")
        except Exception:
            return await self._page.content()

    async def get_interactive_elements(self) -> dict[str, list[str]]:
        """
        Discover interactive elements by role/semantics — not fixed selectors.
        Returns dict: {buttons, links, inputs, selects, tables}.
        """
        self._require_page()

        async def _texts(locator) -> list[str]:
            try:
                count = await locator.count()
                return [
                    (await locator.nth(i).inner_text()).strip()
                    for i in range(min(count, 20))
                ]
            except Exception:
                return []

        buttons = await _texts(self._page.get_by_role("button"))
        links = await _texts(self._page.get_by_role("link"))
        inputs = await self._page.evaluate(
            "() => Array.from(document.querySelectorAll('input,textarea')).map(e => e.placeholder || e.name || e.id || e.type)"
        )
        selects = await _texts(self._page.get_by_role("combobox"))
        tables = await _texts(self._page.get_by_role("table"))

        return {
            "buttons": buttons,
            "links": links[:30],
            "inputs": inputs[:20],
            "selects": selects[:10],
            "tables": tables[:5],
        }

    # ── Authentication ────────────────────────────────────────────────────────

    async def login(
        self,
        portal_url: str,
        username: str,
        password: str,
        *,
        username_selectors: list[str] | None = None,
        password_selectors: list[str] | None = None,
    ) -> bool:
        """
        Attempt portal login. Password is used directly — never logged or sent to LLM.
        Uses semantic locators first; falls back to common selector patterns.
        """
        self._require_page()
        await self.navigate(portal_url)
        await asyncio.sleep(1)

        u_selectors = username_selectors or [
            'input[type="text"]', 'input[name*="user"]',
            'input[name*="email"]', 'input[id*="user"]', 'input[id*="roll"]',
        ]
        p_selectors = password_selectors or [
            'input[type="password"]',
        ]

        filled_user = await self._try_fill(u_selectors, username)
        filled_pass = await self._try_fill(p_selectors, password)   # password NOT logged

        if not filled_user or not filled_pass:
            logger.error("Login form fields not found.")
            return False

        # Click submit — try role-based first
        try:
            submit = self._page.get_by_role("button", name=re.compile(r"log\s*in|sign\s*in|submit", re.I))
            if await submit.count() > 0:
                await submit.first.click()
            else:
                await self._page.keyboard.press("Enter")
        except Exception as exc:
            logger.warning("Submit click failed, trying Enter: %s", exc)
            await self._page.keyboard.press("Enter")

        await self._page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(1)
        return True

    async def _try_fill(self, selectors: list[str], value: str) -> bool:
        for sel in selectors:
            try:
                loc = self._page.locator(sel).first
                if await loc.count() > 0:
                    await loc.fill(value)
                    return True
            except Exception:
                continue
        return False

    # ── Actions ───────────────────────────────────────────────────────────────

    async def click_element(self, description: str) -> bool:
        """
        Click the first element whose visible text matches description (fuzzy, role-based).
        """
        self._require_page()
        try:
            # Try exact text match via role=button
            btn = self._page.get_by_role("button", name=description)
            if await btn.count() > 0:
                await btn.first.click()
                return True
            # Try link
            lnk = self._page.get_by_role("link", name=description)
            if await lnk.count() > 0:
                await lnk.first.click()
                return True
            # Partial text via get_by_text
            txt = self._page.get_by_text(description)
            if await txt.count() > 0:
                await txt.first.click()
                return True
        except Exception as exc:
            logger.warning("click_element failed for '%s': %s", description, exc)
        return False

    async def fill_field(self, label_or_placeholder: str, value: str) -> bool:
        """Fill a form field identified by label text or placeholder. Value not logged."""
        self._require_page()
        try:
            field = self._page.get_by_label(label_or_placeholder)
            if await field.count() > 0:
                await field.first.fill(value)
                return True
            field = self._page.get_by_placeholder(label_or_placeholder)
            if await field.count() > 0:
                await field.first.fill(value)
                return True
        except Exception as exc:
            logger.warning("fill_field failed for '%s': %s", label_or_placeholder, exc)
        return False

    async def submit_form(self) -> None:
        self._require_page()
        await self._page.keyboard.press("Enter")
        await self._page.wait_for_load_state("domcontentloaded")

    async def download_file(self, link_text: str) -> Optional[str]:
        """Click a download link and return the saved file path (temp dir)."""
        self._require_page()
        try:
            async with self._page.expect_download() as download_info:
                lnk = self._page.get_by_role("link", name=link_text)
                if await lnk.count() == 0:
                    lnk = self._page.get_by_text(link_text)
                await lnk.first.click()
            download = await download_info.value
            path = await download.path()
            return str(path)
        except Exception as exc:
            logger.error("Download failed for '%s': %s", link_text, exc)
            return None

    async def get_current_url(self) -> str:
        self._require_page()
        return self._page.url

    async def wait_for_navigation(self, timeout_ms: int = 5000) -> None:
        self._require_page()
        try:
            await self._page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            pass


# Needed for regex inside login
import re

