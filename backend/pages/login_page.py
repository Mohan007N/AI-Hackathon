"""
Login Page Object for portal authentication flows.
Uses semantic Playwright locators: get_by_label(), get_by_role(), get_by_placeholder(), and locator().
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from playwright.async_api import Locator, Page
from pages.base_page import BasePage

logger = logging.getLogger(__name__)


class LoginPage(BasePage):
    """Page Object for student portal login pages."""

    def __init__(self, page: Page):
        super().__init__(page)

    # ── Locators ─────────────────────────────────────────────────────────────

    @property
    def username_input(self) -> Locator:
        """Locates the username/student ID input field using semantic label, placeholder, or ID."""
        by_label = self.get_by_label(re.compile(r"username|user id|student id|roll number|login id|email", re.I))
        by_placeholder = self.get_by_placeholder(re.compile(r"username|student id|roll|email", re.I))
        by_css = self.locator("#username, input[name='username'], input[name='userid'], input[type='text']")
        return by_label.or_(by_placeholder).or_(by_css).first

    @property
    def password_input(self) -> Locator:
        """Locates the password input field using semantic label, placeholder, or type."""
        by_label = self.get_by_label(re.compile(r"password|passcode|secret", re.I))
        by_placeholder = self.get_by_placeholder(re.compile(r"password", re.I))
        by_css = self.locator("#password, input[name='password'], input[type='password']")
        return by_label.or_(by_placeholder).or_(by_css).first

    @property
    def login_button(self) -> Locator:
        """Locates the login/sign-in button using semantic role or ID."""
        by_role = self.get_by_role("button", name=re.compile(r"sign in|log in|login|submit|enter", re.I))
        by_css = self.locator("#login-submit, button[type='submit'], input[type='submit']")
        return by_role.or_(by_css).first

    @property
    def error_banner(self) -> Locator:
        """Locates any error message or alert on login failure."""
        return self.locator(".alert-error, .error-message, .error, #error-msg, [role='alert']").first

    # ── Actions ───────────────────────────────────────────────────────────────

    async def is_on_page(self) -> bool:
        """Verify whether browser is currently on a login page."""
        url_lower = self.url.lower()
        if "/login" in url_lower or "/signin" in url_lower:
            return True
        try:
            return (await self.username_input.count() > 0) and (await self.password_input.count() > 0)
        except Exception:
            return False

    async def fill_username(self, username: str) -> None:
        """Fill username input field."""
        await self.username_input.fill(username)

    async def fill_password(self, password: str) -> None:
        """Fill password input field without logging sensitive values."""
        await self.password_input.fill(password)

    async def click_login(self) -> None:
        """Click the login submit button and await initial load."""
        if await self.login_button.count() > 0:
            await self.login_button.click()
        else:
            await self.page.keyboard.press("Enter")
        await self.wait_for_load_state("domcontentloaded")

    async def login(self, username: str, password: str, timeout_ms: int = 8000) -> bool:
        """
        Execute full login flow using semantic page locators.
        Returns True if login succeeded (left login page), False if still on login or error visible.
        """
        logger.info("Attempting login via LoginPage POM locators.")
        await self.fill_username(username)
        await self.fill_password(password)
        await self.click_login()

        # Wait briefly for navigation
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass

        # Check if login succeeded
        return not (await self.is_on_page())

    async def get_error_message(self) -> Optional[str]:
        """Retrieve error text if authentication failed."""
        if await self.is_visible(self.error_banner):
            return await self.get_text(self.error_banner)
        return None
