"""
Examination Page Object for exam schedule, hall ticket downloads, and admit cards.
Uses semantic Playwright locators: get_by_role(), get_by_text(), and locator().
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional
from playwright.async_api import Locator, Page
from pages.base_page import BasePage

logger = logging.getLogger(__name__)


class ExaminationPage(BasePage):
    """Page Object for Examination and Hall Ticket portals."""

    def __init__(self, page: Page):
        super().__init__(page)

    # ── Locators ─────────────────────────────────────────────────────────────

    @property
    def exam_heading(self) -> Locator:
        """Locates examination or hall ticket headings."""
        by_role = self.get_by_role("heading", name=re.compile(r"exam|hall ticket|admit card|timetable", re.I))
        by_text = self.get_by_text(re.compile(r"examination hall ticket|semester examination", re.I))
        return by_role.or_(by_text).first

    @property
    def download_button(self) -> Locator:
        """Locates the primary PDF download button/link."""
        by_role = self.get_by_role("link", name=re.compile(r"download\s*hall\s*ticket|download\s*pdf|download", re.I))
        by_btn = self.get_by_role("button", name=re.compile(r"download|print", re.I))
        by_id = self.locator("#download-hall-ticket, a[href*='hall_ticket'], a[download*='ticket']")
        return by_role.or_(by_btn).or_(by_id).first

    @property
    def timetable_table(self) -> Locator:
        """Locates exam timetable/schedule tables."""
        return self.locator("table:has-text('Date'), table:has-text('Subject'), table").first

    # ── Actions ───────────────────────────────────────────────────────────────

    async def is_on_page(self) -> bool:
        """Verify whether browser is currently on an examination / hall ticket page."""
        url_lower = self.url.lower()
        if any(k in url_lower for k in ["exam", "hallticket", "hall_ticket", "admitcard", "timetable"]):
            return True
        try:
            return (await self.exam_heading.count() > 0) or (await self.download_button.count() > 0)
        except Exception:
            return False

    async def download_hall_ticket(self, download_dir: str = "downloads", timeout_ms: int = 15000) -> Optional[dict[str, Any]]:
        """
        Trigger hall ticket download, save to local disk, and verify nonzero file size.
        Returns metadata dict: { filename, path, size_bytes }.
        """
        logger.info("Executing hall ticket download via ExaminationPage POM locator.")
        dest_dir = Path(download_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        try:
            async with self.page.expect_download(timeout=timeout_ms) as download_info:
                if await self.download_button.count() > 0:
                    await self.download_button.click()
                else:
                    # Fallback to semantic text click
                    lnk = self.get_by_text(re.compile(r"download|hall ticket", re.I)).first
                    await lnk.click()

            download = await download_info.value
            filename = download.suggested_filename or "hall_ticket.pdf"
            target_path = dest_dir / filename

            try:
                await download.save_as(str(target_path.resolve()))
                final_path = str(target_path.resolve())
            except Exception:
                temp_path = await download.path()
                final_path = str(temp_path) if temp_path else ""

            size = os.path.getsize(final_path) if final_path and os.path.exists(final_path) else 0

            logger.info("ExaminationPage: Hall ticket downloaded: %s (%d bytes)", filename, size)
            return {
                "filename": filename,
                "path": final_path,
                "size_bytes": size,
            }
        except Exception as exc:
            logger.error("ExaminationPage: Download failed: %s", exc)
            return None
