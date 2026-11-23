"""
Course Registration Page Object for Open Elective registration and course enrollment.
Uses semantic Playwright locators: get_by_role(), get_by_label(), and locator().
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional
from playwright.async_api import Locator, Page
from pages.base_page import BasePage

logger = logging.getLogger(__name__)


class CourseRegistrationPage(BasePage):
    """Page Object for elective selection and course enrollment."""

    def __init__(self, page: Page):
        super().__init__(page)

    # ── Locators ─────────────────────────────────────────────────────────────

    @property
    def registration_heading(self) -> Locator:
        """Locates course registration / elective headings."""
        by_role = self.get_by_role("heading", name=re.compile(r"elective|registration|enrollment|courses", re.I))
        by_id = self.locator("#elective-section h2, .registration-header")
        return by_role.or_(by_id).first

    @property
    def course_dropdown(self) -> Locator:
        """Locates the elective selection dropdown <select> element."""
        by_id = self.locator("#course-select, select[name*='course'], select[name*='elective']")
        by_label = self.get_by_label(re.compile(r"course|elective", re.I))
        return by_id.or_(by_label).first

    @property
    def course_code_input(self) -> Locator:
        """Locates text input for course code if a dropdown is not used."""
        by_id = self.locator("#course-code, input[name*='course'], input[name*='code']")
        by_label = self.get_by_label(re.compile(r"course code|subject code", re.I))
        return by_id.or_(by_label).first

    @property
    def enroll_button(self) -> Locator:
        """Locates the submit/enroll action button."""
        by_role = self.get_by_role("button", name=re.compile(r"enroll\s*course|register|submit|enroll", re.I))
        by_id = self.locator("#enroll-btn, button[type='submit']")
        return by_role.or_(by_id).first

    @property
    def confirmation_badge(self) -> Locator:
        """Locates confirmation status indicators."""
        return self.locator(".badge, #confirm-msg").first

    @property
    def confirmation_reference(self) -> Locator:
        """Locates confirmation code or reference ID."""
        return self.locator("strong:has-text('CONF-'), #confirmation-code").first

    # ── Actions ───────────────────────────────────────────────────────────────

    async def is_on_page(self) -> bool:
        """Verify whether browser is currently on course registration page."""
        url_lower = self.url.lower()
        if any(k in url_lower for k in ["register", "elective", "enroll", "course"]):
            return True
        try:
            return (await self.course_dropdown.count() > 0) or (await self.enroll_button.count() > 0)
        except Exception:
            return False

    async def select_course(self, course_value_or_text: str) -> bool:
        """
        Select course from dropdown by value, label, or fuzzy text matching.
        Falls back to filling text input if dropdown is not present.
        """
        # Try dropdown first
        if await self.course_dropdown.count() > 0:
            try:
                # 1. Try select_option by value (e.g. "OE162")
                await self.course_dropdown.select_option(value=course_value_or_text)
                return True
            except Exception:
                pass
            try:
                # 2. Try select_option by label regex
                await self.course_dropdown.select_option(label=re.compile(re.escape(course_value_or_text), re.I))
                return True
            except Exception:
                pass

        # Try course code input field if available
        if await self.course_code_input.count() > 0:
            await self.course_code_input.fill(course_value_or_text)
            return True

        return False

    async def submit_enrollment(self) -> None:
        """Click the enrollment submission button and wait for confirmation."""
        logger.info("Submitting elective course enrollment via POM.")
        await self.enroll_button.click()
        await self.wait_for_load_state("domcontentloaded")

    async def register_elective(self, course_identifier: str) -> dict[str, Any]:
        """
        Complete end-to-end elective registration.
        Returns: { success: bool, confirmation_text: str }
        """
        selected = await self.select_course(course_identifier)
        if not selected:
            return {
                "success": False,
                "error": f"Could not find or select course '{course_identifier}'.",
            }

        await self.submit_enrollment()
        confirmed = await self.is_registration_confirmed()
        details = await self.get_confirmation_details()

        return {
            "success": confirmed,
            "course": course_identifier,
            "details": details,
        }

    async def is_registration_confirmed(self) -> bool:
        """Verify whether course registration was successfully confirmed."""
        page_text = (await self.get_page_text()).lower()
        success_terms = ["confirmed", "successfully registered", "enrollment status: confirmed", "success"]
        return any(term in page_text for term in success_terms)

    async def get_confirmation_details(self) -> str:
        """Extract confirmation ID or message text from post-submission page."""
        if await self.confirmation_reference.count() > 0:
            return await self.get_text(self.confirmation_reference)
        if await self.is_visible(self.confirmation_badge):
            return await self.get_text(self.confirmation_badge)
        text = await self.get_page_text()
        return text[:300] if text else "Registration submitted."
