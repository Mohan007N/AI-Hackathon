"""
Page Object Model (POM) package for CampusPilot browser automation.

Provides reusable semantic page objects:
- BasePage: Core Playwright locators and common actions
- LoginPage: Authentication flows and credentials handling
- DashboardPage: Attendance, schedules, and portal navigation
- ExaminationPage: Exam schedules and Hall Ticket PDF downloads
- CourseRegistrationPage: Open elective selection and registration
"""

from pages.base_page import BasePage
from pages.login_page import LoginPage
from pages.dashboard_page import DashboardPage
from pages.examination_page import ExaminationPage
from pages.course_registration_page import CourseRegistrationPage

__all__ = [
    "BasePage",
    "LoginPage",
    "DashboardPage",
    "ExaminationPage",
    "CourseRegistrationPage",
]

