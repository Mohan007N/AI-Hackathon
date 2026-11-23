"""
Dashboard Page Object for student portal home and overview pages.
Uses semantic Playwright locators: get_by_role(), get_by_text(), and locator().
"""

from __future__ import annotations

import logging
import re
from typing import Any
from playwright.async_api import Locator, Page
from pages.base_page import BasePage

logger = logging.getLogger(__name__)


class DashboardPage(BasePage):
    """Page Object for student portal dashboard."""

    def __init__(self, page: Page):
        super().__init__(page)

    # ── Locators ─────────────────────────────────────────────────────────────

    @property
    def welcome_heading(self) -> Locator:
        """Locates the primary dashboard greeting / welcome header."""
        return self.get_by_role("heading", name=re.compile(r"welcome|student portal|dashboard", re.I)).or_(self.locator("h1")).first

    @property
    def attendance_table(self) -> Locator:
        """Locates the semester attendance table."""
        by_section = self.locator("#attendance-section table")
        by_header = self.locator("table:has-text('Attendance')")
        return by_section.or_(by_header).or_(self.locator("table")).first

    @property
    def hall_ticket_link(self) -> Locator:
        """Locates the hall ticket download button or link."""
        by_role = self.get_by_role("link", name=re.compile(r"hall ticket|admit card|download hall ticket", re.I))
        by_btn = self.get_by_role("button", name=re.compile(r"hall ticket|admit card", re.I))
        by_id = self.locator("#download-hall-ticket")
        return by_role.or_(by_btn).or_(by_id).first

    @property
    def elective_registration_section(self) -> Locator:
        """Locates the open elective registration card/section."""
        return self.locator("#elective-section, form[action*='register'], form[action*='enroll']").first

    @property
    def navigation_links(self) -> Locator:
        """Locates navigation menu links."""
        return self.get_by_role("navigation").get_by_role("link").or_(self.locator("nav a, .nav-item"))

    # ── Actions ───────────────────────────────────────────────────────────────

    async def is_on_page(self) -> bool:
        """Verify whether browser is currently on a student dashboard."""
        url_lower = self.url.lower()
        if "/dashboard" in url_lower or "/home" in url_lower or "/portal" in url_lower:
            return True
        try:
            return (await self.welcome_heading.count() > 0) or (await self.attendance_table.count() > 0)
        except Exception:
            return False

    async def get_welcome_text(self) -> str:
        """Extract the main welcome headline."""
        if await self.welcome_heading.count() > 0:
            return await self.get_text(self.welcome_heading)
        return ""

    async def get_attendance_records(self) -> list[dict[str, str]]:
        """
        Extract parsed attendance rows from the attendance table.
        Returns a list of dictionaries with normalized column headers.
        """
        if await self.attendance_table.count() == 0:
            return []

        try:
            rows_data: list[dict[str, str]] = []
            table = self.attendance_table
            headers: list[str] = []

            # Extract <th> headers
            th_loc = table.locator("th")
            th_count = await th_loc.count()
            for i in range(th_count):
                headers.append((await th_loc.nth(i).inner_text()).strip())

            # Fallback headers if table doesn't have <th>
            if not headers:
                headers = ["Code", "Course", "Attendance", "Status"]

            # Extract <tr> rows
            tr_loc = table.locator("tbody tr")
            if await tr_loc.count() == 0:
                tr_loc = table.locator("tr")

            tr_count = await tr_loc.count()
            # Skip header row if it's included
            start_idx = 1 if (await table.locator("tbody").count() == 0) else 0

            for i in range(start_idx, tr_count):
                td_loc = tr_loc.nth(i).locator("td")
                td_count = await td_loc.count()
                if td_count == 0:
                    continue
                row_dict: dict[str, str] = {}
                for j in range(td_count):
                    key = headers[j] if j < len(headers) else f"col_{j}"
                    row_dict[key] = (await td_loc.nth(j).inner_text()).strip()
                rows_data.append(row_dict)

            return rows_data
        except Exception as exc:
            logger.warning("Failed to parse attendance records from table: %s", exc)
            return []

    async def click_hall_ticket(self) -> None:
        """Click the Hall Ticket download or navigation link."""
        await self.hall_ticket_link.click()
        await self.wait_for_load_state("domcontentloaded")

