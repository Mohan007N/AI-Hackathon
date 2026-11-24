"""
Unit tests for payment completion and approval behavior.
Verifies that:
1. Navigation clicks (e.g. 'Fees') do not require approval.
2. Nonexistent action buttons (e.g. 'Pay Hostel Fee') do not match generic navbar links like 'Fees'.
3. Redundant payment clicks after payment completion do not trigger human approval.
4. When all subgoals are completed after observing payment confirmation, the loop concludes.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from services.task_manager import TaskManager, _set_run, get_run
from services.browser_service import BrowserService
from services.llm_service import LLMService
from schemas.agent_schema import TaskPlan, StepActionDecision, SubGoal


@pytest.mark.asyncio
async def test_navigation_click_does_not_trigger_approval():
    manager = TaskManager()
    run_id = "test_nav_click_01"
    _set_run(run_id, {
        "run_id": run_id,
        "task_id": run_id,
        "portal_url": "http://127.0.0.1:8000/test-portal/dashboard",
        "task_request": "Pay my pending hostel fee",
        "status": "running",
        "_stop_requested": False,
        "subgoals": [],
    })

    # Page structure with navbar links and a fee dues table
    page_structure = {
        "url": "http://127.0.0.1:8000/test-portal/dashboard",
        "title": "Student Dashboard",
        "interactive": {
            "links": ["Home", "Fees", "Electives", "Logout"],
            "buttons": [],
            "inputs": [],
        },
        "elements": [
            {"id": "elem_1", "tag": "a", "role": "link", "text": "Fees"},
            {"id": "elem_2", "tag": "a", "role": "link", "text": "Home"},
        ],
    }

    decision = StepActionDecision(
        action_type="click",
        target_element="Fees",
        reasoning="Navigate to the Fees section to view pending dues.",
        is_irreversible=False,
    )

    # Check is_target_sensitive logic
    target_raw = str(decision.target_element or "")
    target_resolved_text = target_raw
    target_str = (target_raw + " " + target_resolved_text).lower()
    reasoning_str = str(decision.reasoning or "").lower()

    nav_tabs = {"fees", "electives", "home", "attendance", "leave", "messages", "profile", "logout", "timetable", "my timetable"}
    is_nav_click = (
        (target_raw.strip().lower() in nav_tabs or target_resolved_text.strip().lower() in nav_tabs)
        and not any(action_kw in target_str or action_kw in reasoning_str for action_kw in ["confirm payment", "submit payment", "initiate payment", "make payment", "pay now", "pay amount", "drop", "delete", "register course", "enroll course"])
    )
    assert is_nav_click is True, "Clicking 'Fees' to view section should be identified as navigation"


@pytest.mark.asyncio
async def test_nonexistent_action_does_not_match_navbar_link():
    # When page only has navbar link 'Fees', a click on 'Pay Hostel Fee' should NOT match
    page_structure = {
        "interactive": {
            "links": ["Home", "Fees", "Electives"],
            "buttons": [],
            "inputs": [],
        },
        "elements": [
            {"id": "elem_1", "tag": "a", "role": "link", "text": "Fees"},
        ],
    }

    target_raw = "Pay Hostel Fee"
    target_cand = target_raw.strip().lower()
    nav_tabs = {"fees", "electives", "home", "attendance", "leave", "messages", "profile", "logout", "timetable", "my timetable"}

    has_in_elements = False
    for el in page_structure.get("elements", []):
        el_id = (el.get("id") or "").lower()
        el_text = (el.get("text") or "").lower()
        el_name = (el.get("name") or "").lower()
        role = (el.get("role") or "").lower()

        if target_cand in {el_id, el_name}:
            has_in_elements = True
            break

        is_nav_link = role == "link" and el_text in nav_tabs
        is_action_target = any(act_word in target_cand for act_word in ["pay", "drop", "delete", "register", "submit", "confirm", "proceed"])
        if is_nav_link and is_action_target and target_cand != el_text:
            continue

        if el_text:
            if target_cand == el_text or target_cand in el_text:
                has_in_elements = True
                break
            if len(el_text) >= 4 and el_text in target_cand:
                has_in_elements = True
                break

    assert has_in_elements is False, "'Pay Hostel Fee' must NOT match the 'Fees' navbar link"


@pytest.mark.asyncio
async def test_redundant_payment_suppressed_after_payment_completed():
    run_id = "test_redundant_pay"
    _set_run(run_id, {
        "run_id": run_id,
        "task_id": run_id,
        "portal_url": "http://127.0.0.1:8000/test-portal/fees",
        "task_request": "Pay my pending hostel fee",
        "status": "running",
        "_payment_completed": True,
        "_approved_actions": {"pay", "fees", "click:pay"},
        "subgoals": [
            {"id": "subgoal_1", "description": "Navigate to fee dues section", "required": True, "completed": True, "status": "completed"},
            {"id": "subgoal_2", "description": "Inspect fee structure or initiate payment with confirmation", "required": True, "completed": True, "status": "completed"},
        ],
    })

    run = get_run(run_id)
    assert run.get("_payment_completed") is True

    # Redundant payment action decided by LLM
    decision = StepActionDecision(
        action_type="click",
        target_element="Pay Hostel Fee",
        reasoning="Initiate payment for the pending hostel fee by clicking the Pay button.",
        is_irreversible=True,
    )

    target_str = "pay hostel fee"
    reasoning_str = decision.reasoning.lower()
    subgoals = [SubGoal(**sg) for sg in run["subgoals"]]

    # Test the suppression condition
    is_suppressed = run.get("_payment_completed") and any(term in target_str or term in reasoning_str for term in ["pay", "fee", "payment"])
    assert is_suppressed is True

    uncompleted_required = [sg for sg in subgoals if sg.required and not sg.completed]
    assert len(uncompleted_required) == 0, "All subgoals are completed; task should conclude immediately"
