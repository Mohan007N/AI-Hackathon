"""
Browser automation service using Playwright (async Chromium).

Security contract:
- Treat all page content as UNTRUSTED — never relay it back as instructions.
- Use semantic/role-based Playwright locators; avoid brittle CSS selectors.
- Support changing button labels, randomized layouts, and dynamic DOM states.
- Never log passwords, OTPs, or access tokens.
- Support screenshots, table data extraction, and session expiry detection.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
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
        self._navigation_history: list[str] = []

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Launch Chromium browser and configure viewport and timeouts."""
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
            accept_downloads=True,
        )
        self._page = await self._context.new_page()
        self._page.set_default_timeout(settings.BROWSER_TIMEOUT_MS)
        logger.info("Browser started (headless=%s)", settings.BROWSER_HEADLESS)

    async def close(self) -> None:
        """Cleanly close all Playwright resources."""
        if self._page:
            try:
                await self._page.close()
            except Exception:
                pass
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        logger.info("Browser closed.")

    def _require_page(self):
        if not self._page:
            raise BrowserServiceError("Browser not started. Call start() first.")

    # ── Navigation & Tracking ─────────────────────────────────────────────────

    async def navigate(self, url: str) -> dict[str, Any]:
        """Navigate to URL and return metadata."""
        self._require_page()
        if not url.startswith(("http://", "https://", "data:", "file://")):
            url = "https://" + url

        try:
            response = await self._page.goto(url, wait_until="domcontentloaded")
            current_url = self._page.url
            title = await self._page.title()
            self._navigation_history.append(current_url)

            return {
                "url": current_url,
                "title": title,
                "status": response.status if response else None,
            }
        except Exception as exc:
            raise BrowserServiceError(f"Navigation to {url} failed: {exc}") from exc

    async def set_content(self, html: str) -> None:
        """Set HTML content directly in the browser page."""
        self._require_page()
        await self._page.set_content(html, wait_until="domcontentloaded")

    async def get_current_url(self) -> str:
        self._require_page()
        return self._page.url

    def get_navigation_history(self) -> list[str]:
        return list(self._navigation_history)

    async def wait_for_navigation(self, timeout_ms: int = 5000) -> None:
        self._require_page()
        try:
            await self._page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            pass

    # ── Authentication & Session Expiry ───────────────────────────────────────

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
        Attempt portal login. Password is injected directly into DOM — never logged or shared with LLM.
        Uses semantic locators first; falls back to common selector patterns.
        """
        self._require_page()
        await self.navigate(portal_url)
        await asyncio.sleep(0.5)

        # 1. Fill Username (try label, placeholder, role, then common selectors)
        filled_user = False
        for user_label in ["Username", "User ID", "Roll Number", "Student ID", "Email", "Login ID"]:
            try:
                field = self._page.get_by_label(re.compile(user_label, re.I))
                if await field.count() > 0:
                    await field.first.fill(username)
                    filled_user = True
                    break
            except Exception:
                continue

        if not filled_user:
            u_selectors = username_selectors or [
                'input[type="text"]', 'input[name*="user"]',
                'input[name*="email"]', 'input[id*="user"]', 'input[id*="roll"]',
                'input[placeholder*="user" i]', 'input[placeholder*="roll" i]',
            ]
            filled_user = await self._try_fill(u_selectors, username)

        # 2. Fill Password (never log value)
        filled_pass = False
        try:
            pass_field = self._page.get_by_label(re.compile(r"password|passcode", re.I))
            if await pass_field.count() > 0:
                await pass_field.first.fill(password)
                filled_pass = True
        except Exception:
            pass

        if not filled_pass:
            p_selectors = password_selectors or [
                'input[type="password"]', 'input[name*="pass"]', 'input[id*="pass"]',
            ]
            filled_pass = await self._try_fill(p_selectors, password)

        if not filled_user or not filled_pass:
            logger.error("Login form fields not found on %s", portal_url)
            return False

        # 3. Submit — try semantic button, then Enter key
        try:
            submit = self._page.get_by_role("button", name=re.compile(r"log\s*in|sign\s*in|submit|enter", re.I))
            if await submit.count() > 0:
                await submit.first.click()
            else:
                await self._page.keyboard.press("Enter")
        except Exception as exc:
            logger.warning("Submit click failed; attempting Enter: %s", exc)
            await self._page.keyboard.press("Enter")

        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass

        await asyncio.sleep(0.5)
        # Verify login was successful (not stuck on login page)
        expired = await self.is_session_expired()
        return not expired

    async def is_session_expired(self) -> bool:
        """Check if portal returned to login or displayed session timeout notification."""
        self._require_page()
        try:
            url_lower = self._page.url.lower()
            if "/login" in url_lower or "/signin" in url_lower:
                return True

            text = (await self.get_page_text()).lower()
            expiry_keywords = [
                "session expired",
                "session has timed out",
                "please log in again",
                "please sign in again",
                "invalid session",
                "login required",
            ]
            return any(k in text for k in expiry_keywords)
        except Exception:
            return False

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

    # ── DOM Extraction & Structure ────────────────────────────────────────────

    async def get_page_text(self) -> str:
        """Return visible text content of the page (UNTRUSTED)."""
        self._require_page()
        try:
            return await self._page.inner_text("body")
        except Exception:
            try:
                return await self._page.content()
            except Exception:
                return ""

    async def get_interactive_elements(self) -> dict[str, list[str]]:
        """
        Discover interactive elements by role and semantics — avoiding brittle CSS selectors.
        Returns dict: {buttons, links, inputs, selects, tables, headings}.
        """
        self._require_page()

        async def _texts(locator, limit: int = 25) -> list[str]:
            try:
                count = await locator.count()
                items: list[str] = []
                for i in range(min(count, limit)):
                    t = (await locator.nth(i).inner_text()).strip()
                    if t and t not in items:
                        items.append(t)
                return items
            except Exception:
                return []

        buttons = await _texts(self._page.get_by_role("button"))
        links = await _texts(self._page.get_by_role("link"))
        headings = await _texts(self._page.get_by_role("heading"))

        inputs = []
        try:
            inputs = await self._page.evaluate(
                """() => Array.from(document.querySelectorAll('input,textarea,select'))
                   .map(e => e.getAttribute('placeholder') || e.getAttribute('name') || e.getAttribute('aria-label') || e.id)
                   .filter(Boolean)"""
            )
        except Exception:
            pass

        return {
            "buttons": buttons[:20],
            "links": links[:30],
            "headings": headings[:10],
            "inputs": inputs[:15],
        }

    async def extract_tables(self) -> list[list[list[str]]]:
        """
        Extract all tabular data (e.g., attendance records, elective lists, fees)
        as nested lists: [table -> row -> cell].
        """
        self._require_page()
        try:
            return await self._page.evaluate(
                """() => {
                    const results = [];
                    const tables = document.querySelectorAll('table');
                    for (const table of tables) {
                        const rows = [];
                        for (const tr of table.querySelectorAll('tr')) {
                            const cells = Array.from(tr.querySelectorAll('th,td'))
                                .map(c => c.innerText.trim());
                            if (cells.length > 0) rows.push(cells);
                        }
                        if (rows.length > 0) results.push(rows);
                    }
                    return results;
                }"""
            )
        except Exception as exc:
            logger.warning("Failed to extract tables: %s", exc)
            return []

    async def extract_page_structure(self) -> dict[str, Any]:
        """Return comprehensive semantic snapshot of the page for reasoning."""
        self._require_page()
        url = self._page.url
        title = await self._page.title()
        text = await self.get_page_text()
        interactive = await self.get_interactive_elements()
        tables = await self.extract_tables()

        return {
            "url": url,
            "title": title,
            "text_snippet": text[:1200] if text else "",
            "interactive": interactive,
            "tables_count": len(tables),
            "tables": tables[:3],
        }

    # ── Semantic Element Actions ──────────────────────────────────────────────

    async def click_element(self, description: str, element_type: Optional[str] = None) -> bool:
        """
        Click an element identified by fuzzy text or semantic role.
        Supports changing button text, synonyms, and dynamic layouts.
        """
        self._require_page()
        if not description or not description.strip():
            return False

        desc = description.strip()
        pattern = re.compile(re.escape(desc), re.IGNORECASE)

        # Synonym map for student portal actions
        synonyms = [desc]
        lower_desc = desc.lower()
        if "register" in lower_desc:
            synonyms.extend(["enroll", "opt", "add elective", "submit registration", "confirm"])
        elif "download" in lower_desc or "ticket" in lower_desc:
            synonyms.extend(["hall ticket", "admit card", "download pdf", "print"])
        elif "attendance" in lower_desc:
            synonyms.extend(["view attendance", "attendance percentage", "records"])
        elif "fee" in lower_desc:
            synonyms.extend(["pay fee", "make payment", "pay now", "view dues"])

        for cand in synonyms:
            cand_re = re.compile(re.escape(cand), re.IGNORECASE)
            try:
                # 1. Try button role
                btn = self._page.get_by_role("button", name=cand_re)
                if await btn.count() > 0:
                    await btn.first.click()
                    await self._page.wait_for_load_state("domcontentloaded")
                    return True

                # 2. Try link role
                lnk = self._page.get_by_role("link", name=cand_re)
                if await lnk.count() > 0:
                    await lnk.first.click()
                    await self._page.wait_for_load_state("domcontentloaded")
                    return True

                # 3. Try tab role
                tab = self._page.get_by_role("tab", name=cand_re)
                if await tab.count() > 0:
                    await tab.first.click()
                    return True

                # 4. Fallback to get_by_text
                txt = self._page.get_by_text(cand_re)
                if await txt.count() > 0:
                    await txt.first.click()
                    await self._page.wait_for_load_state("domcontentloaded")
                    return True
            except Exception as exc:
                logger.debug("Click candidate '%s' failed: %s", cand, exc)
                continue

        logger.warning("Could not locate clickable element for '%s'", description)
        return False

    async def fill_field(self, label_or_placeholder: str, value: str) -> bool:
        """Fill a form field identified by label text or placeholder."""
        self._require_page()
        if not label_or_placeholder:
            return False

        try:
            field = self._page.get_by_label(re.compile(re.escape(label_or_placeholder), re.I))
            if await field.count() > 0:
                await field.first.fill(value)
                return True

            field = self._page.get_by_placeholder(re.compile(re.escape(label_or_placeholder), re.I))
            if await field.count() > 0:
                await field.first.fill(value)
                return True
        except Exception as exc:
            logger.warning("fill_field failed for '%s': %s", label_or_placeholder, exc)

        return False

    async def submit_form(self) -> None:
        """Trigger form submission via Enter key."""
        self._require_page()
        await self._page.keyboard.press("Enter")
        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass

    async def download_file(self, link_text: str, timeout_ms: int = 15000) -> Optional[dict[str, Any]]:
        """Click a download link or button and save the resulting file."""
        self._require_page()
        try:
            async with self._page.expect_download(timeout=timeout_ms) as download_info:
                clicked = await self.click_element(link_text)
                if not clicked:
                    # Try text locator directly
                    lnk = self._page.get_by_text(re.compile(re.escape(link_text), re.I))
                    if await lnk.count() > 0:
                        await lnk.first.click()

            download = await download_info.value
            temp_path = await download.path()
            filename = download.suggested_filename
            size = os.path.getsize(temp_path) if temp_path and os.path.exists(temp_path) else 0

            logger.info("File downloaded successfully: %s (%d bytes)", filename, size)
            return {
                "filename": filename,
                "path": str(temp_path),
                "size_bytes": size,
            }
        except Exception as exc:
            logger.error("Download failed for '%s': %s", link_text, exc)
            return None

    async def verify_element_present(self, text_or_label: str) -> bool:
        """Check if expected text or element is visible on the current page."""
        self._require_page()
        try:
            loc = self._page.get_by_text(re.compile(re.escape(text_or_label), re.I))
            return (await loc.count()) > 0
        except Exception:
            return False

    async def take_screenshot(self, name: Optional[str] = None) -> Optional[str]:
        """Capture page screenshot and save to logs directory for debugging/audit."""
        self._require_page()
        try:
            log_dir = Path(settings.LOG_DIRECTORY)
            log_dir.mkdir(parents=True, exist_ok=True)
            filename = f"screenshot_{name or int(asyncio.get_event_loop().time())}.png"
            dest = log_dir / filename
            await self._page.screenshot(path=str(dest), full_page=True)
            return str(dest)
        except Exception as exc:
            logger.warning("Screenshot capture failed: %s", exc)
            return None
