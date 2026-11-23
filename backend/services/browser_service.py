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
        self._element_registry: dict[str, dict[str, Any]] = {}

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
            self._page = None
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        logger.info("Browser closed.")

    def _require_page(self):
        if not self._page:
            raise BrowserServiceError("Browser not started. Call start() first.")

    @property
    def page(self):
        """Direct access to the underlying Playwright Page instance."""
        return self._page

    def as_page(self, page_cls):
        """Instantiate any Page Object on the active Playwright page."""
        self._require_page()
        return page_cls(self._page)

    @property
    def login_page(self):
        """Access LoginPage Page Object on current page."""
        self._require_page()
        from pages.login_page import LoginPage
        return LoginPage(self._page)

    @property
    def dashboard_page(self):
        """Access DashboardPage Page Object on current page."""
        self._require_page()
        from pages.dashboard_page import DashboardPage
        return DashboardPage(self._page)

    @property
    def examination_page(self):
        """Access ExaminationPage Page Object on current page."""
        self._require_page()
        from pages.examination_page import ExaminationPage
        return ExaminationPage(self._page)

    @property
    def course_registration_page(self):
        """Access CourseRegistrationPage Page Object on current page."""
        self._require_page()
        from pages.course_registration_page import CourseRegistrationPage
        return CourseRegistrationPage(self._page)

    # ── Navigation & Tracking ─────────────────────────────────────────────────

    async def navigate(self, url: str) -> dict[str, Any]:
        """Navigate to URL and return metadata with strict scheme and redirect validation (Reqs 11 & 12)."""
        self._require_page()
        url_clean = url.strip()
        url_lower = url_clean.lower()

        # Requirement 12: Restrict schemes strictly to http:// and https://
        if not (url_lower.startswith("http://") or url_lower.startswith("https://")):
            raise BrowserServiceError(
                f"Blocked navigation to '{url}': Only HTTP and HTTPS schemes are allowed (file, data, javascript blocked)."
            )

        try:
            response = await self._page.goto(url_clean, wait_until="domcontentloaded")
            # Wait briefly for client-side JavaScript redirect or network to settle
            try:
                await self._page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass
            current_url = self._page.url
            title = await self._page.title()
            self._navigation_history.append(current_url)

            # Requirement 11: Validate redirects
            from services.url_validator import URLValidator
            validator = URLValidator()
            redirect_check = validator.validate_redirect(url_clean, current_url)
            if redirect_check.status == "blocked":
                raise BrowserServiceError(f"Navigation blocked by redirect security policy: {redirect_check.reason}")

            return {
                "url": current_url,
                "title": title,
                "status": response.status if response else None,
            }
        except BrowserServiceError:
            raise
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
        current_u = (self._page.url or "").rstrip("/")
        target_u = (portal_url or "").rstrip("/")
        if not current_u or (current_u != target_u and not current_u.startswith(target_u)):
            await self.navigate(portal_url)
            await asyncio.sleep(0.5)

        # 0. Attempt semantic Page Object Model login first if supported on this page
        try:
            if await self.login_page.is_on_page():
                pom_success = await self.login_page.login(username, password, timeout_ms=5000)
                if pom_success:
                    return True
        except Exception as pom_err:
            logger.debug("POM login attempt bypassed; falling back to dynamic fuzzy locators: %s", pom_err)

        # 1. Fill Username (accessible labels, role, placeholder, explicit attributes — never generic text inputs)
        filled_user = False
        for user_label in ["Username", "User ID", "Roll Number", "Student ID", "Email", "Login ID", "Enrollment Number"]:
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
                'input[autocomplete="username"]',
                'input[name*="user" i]',
                'input[name*="email" i]',
                'input[name*="login" i]',
                'input[id*="user" i]',
                'input[id*="roll" i]',
                'input[id*="email" i]',
                'input[id*="login" i]',
                'input[placeholder*="user" i]',
                'input[placeholder*="roll" i]',
                'input[placeholder*="email" i]',
                'input[placeholder*="student" i]',
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
                'input[type="password"]',
                'input[autocomplete="current-password"]',
                'input[name*="pass" i]',
                'input[id*="pass" i]',
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
        # Verify actual authentication success (not error banner and not stuck on login form)
        authenticated = await self.is_authenticated()
        logger.info("Login verification on %s: authenticated=%s", portal_url, authenticated)
        return authenticated

    async def is_authenticated(self) -> bool:
        """
        Detect actual authentication success.
        Returns False if login error banner is visible, password field is still present,
        or current page is a login/signin page without dashboard elements.
        Returns True if dashboard, logout, or session user elements are visible.
        """
        self._require_page()
        try:
            # 1. Check for explicit login failure / error messages
            text = (await self.get_page_text()).lower()
            error_keywords = [
                "invalid credentials", "invalid username or password",
                "incorrect password", "wrong password", "login failed",
                "authentication failed", "user not found", "access denied",
                "bad credentials"
            ]
            if any(k in text for k in error_keywords):
                logger.warning("Authentication failed: error banner detected on page")
                return False

            # 2. Check if password field is still present and visible on page
            pass_inputs = self._page.locator('input[type="password"]')
            if await pass_inputs.count() > 0:
                for idx in range(await pass_inputs.count()):
                    if await pass_inputs.nth(idx).is_visible():
                        logger.warning("Authentication uncertain: password field still visible on page")
                        return False

            # 3. Check for positive authentication indicators
            auth_indicators = [
                self._page.get_by_role("button", name=re.compile(r"log\s*out|sign\s*out", re.I)),
                self._page.get_by_role("link", name=re.compile(r"log\s*out|sign\s*out", re.I)),
                self._page.locator("#dashboard, .dashboard, [data-testid='dashboard']"),
                self._page.get_by_role("heading", name=re.compile(r"dashboard|welcome|student portal|overview", re.I)),
            ]
            for indicator in auth_indicators:
                if await indicator.count() > 0 and await indicator.first.is_visible():
                    return True

            # 4. If URL indicates login/signin and no positive auth indicator, not authenticated
            url_lower = self._page.url.lower()
            if "/login" in url_lower or "/signin" in url_lower:
                return False

            return True
        except Exception as exc:
            logger.warning("Error checking authentication status: %s", exc)
            return False

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
        """
        Return comprehensive semantic snapshot of the page for reasoning (Issue C).
        Assigns temporary element IDs (elem_1, elem_2, ...) to observed interactive elements.
        """
        self._require_page()
        url = self._page.url
        title = await self._page.title()
        text = await self.get_page_text()

        # Extract structured interactive elements with temporary element IDs
        structured_elements = []
        try:
            structured_elements = await self._page.evaluate(
                """() => {
                    let counter = 1;
                    const items = [];
                    // Reset previous data-agent-id tags
                    document.querySelectorAll('[data-agent-id]').forEach(el => el.removeAttribute('data-agent-id'));

                    const candidates = document.querySelectorAll(
                        'button, a[href], input, select, textarea, [role="button"], [role="link"], [role="tab"]'
                    );

                    for (const el of candidates) {
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0' || el.offsetParent === null) {
                            continue;
                        }

                        const eid = 'elem_' + (counter++);
                        el.setAttribute('data-agent-id', eid);

                        const tag = el.tagName.toLowerCase();
                        const role = el.getAttribute('role') || (tag === 'a' ? 'link' : tag === 'button' ? 'button' : tag);
                        const txt = (el.innerText || el.value || '').trim().replace(/\\s+/g, ' ');
                        const placeholder = el.getAttribute('placeholder') || '';
                        const name = el.getAttribute('name') || '';
                        const ariaLabel = el.getAttribute('aria-label') || '';
                        const type = el.getAttribute('type') || '';

                        items.push({
                            id: eid,
                            tag: tag,
                            role: role,
                            text: txt.slice(0, 100),
                            placeholder: placeholder,
                            name: name,
                            aria_label: ariaLabel,
                            type: type,
                        });
                        if (items.length >= 60) break;
                    }
                    return items;
                }"""
            )
        except Exception as exc:
            logger.debug("Structured element extraction fallback: %s", exc)

        # Update element registry
        self._element_registry = {item["id"]: item for item in structured_elements if "id" in item}

        interactive = await self.get_interactive_elements()
        tables = await self.extract_tables()

        return {
            "url": url,
            "title": title,
            "text_snippet": text[:1200] if text else "",
            "elements": structured_elements,
            "interactive": interactive,
            "tables_count": len(tables),
            "tables": tables[:3],
        }

    # ── Semantic Element Actions ──────────────────────────────────────────────

    async def click_element(self, description: str, element_type: Optional[str] = None) -> bool:
        """
        Click an element identified by temporary element_id (e.g. elem_1) or fuzzy semantic text.
        Supports changing button text, synonyms, and dynamic layouts.
        """
        self._require_page()
        if not description or not description.strip():
            return False

        desc = description.strip()

        # 0. Check if description is a temporary element_id (e.g. elem_1, elem_14)
        if re.match(r"^elem_\d+$", desc):
            try:
                loc = self._page.locator(f"[data-agent-id='{desc}']")
                if await loc.count() > 0:
                    await loc.first.click()
                    await self._page.wait_for_load_state("domcontentloaded")
                    try:
                        await self._page.wait_for_load_state("networkidle", timeout=2500)
                    except Exception:
                        pass
                    return True
            except Exception as eid_err:
                logger.debug("Direct click by element_id '%s' failed: %s", desc, eid_err)

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
        """Fill a form field identified by temporary element_id, label text, or placeholder."""
        self._require_page()
        if not label_or_placeholder:
            return False

        target = label_or_placeholder.strip()

        # 0. Check if target is a temporary element_id (e.g. elem_2)
        if re.match(r"^elem_\d+$", target):
            try:
                loc = self._page.locator(f"[data-agent-id='{target}']")
                if await loc.count() > 0:
                    await loc.first.fill(value)
                    return True
            except Exception as eid_err:
                logger.debug("Direct fill by element_id '%s' failed: %s", target, eid_err)

        try:
            field = self._page.get_by_label(re.compile(re.escape(target), re.I))
            if await field.count() > 0:
                await field.first.fill(value)
                return True

            field = self._page.get_by_placeholder(re.compile(re.escape(target), re.I))
            if await field.count() > 0:
                await field.first.fill(value)
                return True
        except Exception as exc:
            logger.warning("fill_field failed for '%s': %s", target, exc)

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
            filename = download.suggested_filename or "downloaded_file.pdf"
            downloads_dir = Path("downloads")
            downloads_dir.mkdir(parents=True, exist_ok=True)
            target_path = downloads_dir / filename
            try:
                await download.save_as(str(target_path.resolve()))
                final_path = str(target_path.resolve())
            except Exception:
                temp_path = await download.path()
                final_path = str(temp_path) if temp_path else ""
            size = os.path.getsize(final_path) if final_path and os.path.exists(final_path) else 0

            logger.info("File downloaded successfully: %s (%d bytes)", filename, size)
            return {
                "filename": filename,
                "path": final_path,
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

    async def take_screenshot_bytes(self) -> Optional[bytes]:
        """Capture page screenshot directly as PNG bytes for live API view."""
        if not self._page:
            return None
        try:
            return await self._page.screenshot(type="png", full_page=False)
        except Exception as exc:
            logger.debug("Screenshot capture bytes failed: %s", exc)
            return None
