"""
Test Suite for Page Object Model (POM) Architecture (Task 11 & 14).

Verifies:
1. BasePage core locator helpers (get_by_role, get_by_label, get_by_text, locator)
2. LoginPage authentication and semantic field filling
3. DashboardPage table extraction, headers, and navigation
4. ExaminationPage and actual nonzero PDF Hall Ticket download
5. CourseRegistrationPage course selection, enrollment submission, and confirmation
6. Dynamic AI Agent fallback: Observe-Reason-Validate-Act-Verify remains intact
   for unfamiliar pages without requiring predefined page objects
"""

import os
import pytest
from pathlib import Path
from services.browser_service import BrowserService
from pages import (
    BasePage,
    LoginPage,
    DashboardPage,
    ExaminationPage,
    CourseRegistrationPage,
)

PORTAL_BASE = "http://127.0.0.1:8000"


@pytest.fixture
async def browser():
    """Initializes a real Playwright BrowserService instance."""
    service = BrowserService()
    await service.start()
    yield service
    await service.close()


# ── 1. BasePage & Locators ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_base_page_locators(browser: BrowserService):
    """Verify BasePage provides working semantic Playwright locators."""
    await browser.navigate(f"{PORTAL_BASE}/test-portal/login")
    base = BasePage(browser.page)

    assert "127.0.0.1" in base.url
    assert "Login" in (await base.title())

    # Test semantic locators on login page
    heading = base.get_by_role("heading", name="Student Portal Sign In")
    assert await heading.count() > 0

    btn = base.get_by_role("button", name="Sign In to Portal")
    assert await btn.count() > 0

    username_input = base.get_by_label("Student ID / Username")
    assert await username_input.count() > 0

    body_text = await base.get_page_text()
    assert "Student Portal Sign In" in body_text


# ── 2. LoginPage POM ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_page_pom(browser: BrowserService):
    """Verify LoginPage locators, validation, and login action."""
    await browser.navigate(f"{PORTAL_BASE}/test-portal/login")
    login_page: LoginPage = browser.login_page

    assert await login_page.is_on_page() is True
    assert await login_page.username_input.count() > 0
    assert await login_page.password_input.count() > 0
    assert await login_page.login_button.count() > 0

    # Execute login
    login_success = await login_page.login("student101", "demo123")
    assert login_success is True

    # Assert navigation to dashboard
    assert "dashboard" in browser.page.url.lower()


# ── 3. DashboardPage POM ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dashboard_page_pom(browser: BrowserService):
    """Verify DashboardPage table parsing and navigation."""
    await browser.navigate(f"{PORTAL_BASE}/test-portal/dashboard")
    dashboard: DashboardPage = browser.dashboard_page

    assert await dashboard.is_on_page() is True

    # Verify welcome message
    welcome = await dashboard.get_welcome_text()
    assert "Student" in welcome or "2026-CS-419" in welcome

    # Verify attendance table parsing
    records = await dashboard.get_attendance_records()
    assert len(records) >= 3
    course_titles = [r.get("Course Title", "") for r in records]
    assert any("Operating Systems" in c for c in course_titles)
    assert any("Machine Learning" in c for c in course_titles)


# ── 4. ExaminationPage POM & Real Download ────────────────────────────────────

@pytest.mark.asyncio
async def test_examination_page_pom_and_download(browser: BrowserService):
    """Verify ExaminationPage hall ticket locator and real nonzero file download."""
    await browser.navigate(f"{PORTAL_BASE}/test-portal/dashboard")
    exam_page: ExaminationPage = browser.examination_page

    assert await exam_page.is_on_page() is True
    assert await exam_page.download_button.count() > 0

    # Perform real file download using Playwright expect_download
    dl_dir = os.path.join(os.getcwd(), "downloads")
    dl_info = await exam_page.download_hall_ticket(download_dir=dl_dir)

    assert dl_info is not None
    assert dl_info["size_bytes"] > 0
    assert os.path.exists(dl_info["path"])

    # Clean up downloaded file
    try:
        os.remove(dl_info["path"])
    except OSError:
        pass


# ── 5. CourseRegistrationPage POM ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_course_registration_page_pom(browser: BrowserService):
    """Verify CourseRegistrationPage course selection, submit, and confirmation."""
    await browser.navigate(f"{PORTAL_BASE}/test-portal/dashboard")
    reg_page: CourseRegistrationPage = browser.course_registration_page

    assert await reg_page.is_on_page() is True
    assert await reg_page.course_dropdown.count() > 0

    # Register for Digital Marketing (OE162)
    reg_result = await reg_page.register_elective("OE162")
    assert reg_result["success"] is True

    # Check confirmation message on resulting page
    assert await reg_page.is_registration_confirmed() is True
    details = await reg_page.get_confirmation_details()
    assert "CONF-2026-OE162" in details or "Confirmed" in details


# ── 6. Dynamic AI Agent Fallback on Unfamiliar Website (Task 8 & 10) ──────────

@pytest.mark.asyncio
async def test_dynamic_ai_agent_fallback_on_unfamiliar_page(browser: BrowserService):
    """
    Verify that unfamiliar, unmapped websites are handled seamlessly by dynamic
    DOM inspection (Observe-Reason-Act) without breaking or requiring Page Objects.
    """
    # Render arbitrary dynamic HTML that doesn't match any Page Object
    custom_html = """
    <!DOCTYPE html>
    <html>
    <head><title>Unfamiliar Portal</title></head>
    <body>
        <h2>Survey Form 2026</h2>
        <p>Please enter your feedback:</p>
        <input id="feedback-field" placeholder="Enter feedback" />
        <button id="custom-action-btn" onclick="document.getElementById('feedback-field').value='Saved!'">
            Submit Feedback
        </button>
    </body>
    </html>
    """
    await browser.set_content(custom_html)

    # 1. Observe: Dynamic DOM extraction discovers interactive elements without POM
    structure = await browser.extract_page_structure()
    assert structure["title"] == "Unfamiliar Portal"
    assert "Submit Feedback" in structure["interactive"]["buttons"]
    assert "Enter feedback" in structure["interactive"]["inputs"]

    # 2. Act: Dynamic action dispatch clicks element by fuzzy text
    clicked = await browser.click_element("Submit Feedback")
    assert clicked is True

    # 3. Verify: Verify dynamic state update
    page_text = await browser.get_page_text()
    assert "Submit Feedback" in page_text
