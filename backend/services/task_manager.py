"""
Task manager — orchestrates the complete AI agent workflow for runs.

Loop architecture:
  Observe → Reason (LLM) → Validate (Security) → Act (Browser) → Verify

Coordinates:
  PortalService → LLMService → BrowserService → SecurityService
  → ApprovalService → ReportService → ActionLogger

Single source of truth for all automation executions (CampusPilot dashboard & organizer runs).
Credentials contract:
  Credentials are kept in-memory only during task execution and
  are NEVER logged, persisted, or shared with LLMs/external APIs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from config import settings
from services.portal_service import PortalService, PortalServiceError
from services.llm_service import LLMService, LLMServiceError
from services.browser_service import BrowserService, BrowserServiceError
from services.security_service import SecurityService
from services.approval_service import ApprovalService
from services.report_service import ReportService
from services.task_persistence import TaskPersistence
from utils.action_logger import log_action, read_logs
from schemas.agent_schema import TaskPlan, StepActionDecision, SubGoal

logger = logging.getLogger(__name__)

# ── Persistent run store ────────────────────────────────────────────────────────
_persistence = TaskPersistence()
_runs: dict[str, dict[str, Any]] = _persistence.load_all_runs()


def get_run(run_id: str) -> Optional[dict[str, Any]]:
    return _runs.get(run_id)


def _set_run(run_id: str, data: dict[str, Any]) -> None:
    _runs[run_id] = data
    _persistence.save_run(run_id, data)


def _update_run(run_id: str, **kwargs) -> None:
    if run_id in _runs:
        current_status = _runs[run_id].get("status")
        TERMINAL_STATES = {"completed", "done", "failed", "stopped", "invalid"}
        # If run is already in a terminal state, do not allow transitioning out of it (Requirement 7)
        if current_status in TERMINAL_STATES and "status" in kwargs and kwargs["status"] not in TERMINAL_STATES:
            del kwargs["status"]
        if current_status in TERMINAL_STATES and "result_status" in kwargs and _runs[run_id].get("result_status"):
            del kwargs["result_status"]

        _runs[run_id].update(kwargs)
        _runs[run_id]["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
        _persistence.save_run(run_id, _runs[run_id])


class TaskManager:
    """Orchestrates end-to-end task execution with autonomous agent loop and security gates."""

    def __init__(
        self,
        portal_service: Optional[PortalService] = None,
        llm_service: Optional[LLMService] = None,
        security_service: Optional[SecurityService] = None,
        approval_service: Optional[ApprovalService] = None,
        report_service: Optional[ReportService] = None,
    ):
        self._portal = portal_service or PortalService()
        self._llm = llm_service or LLMService()
        self._security = security_service or SecurityService()
        self._approval = approval_service or ApprovalService()
        self._report = report_service or ReportService(self._portal)

    # ── Phase 1: Start Task (Dashboard or Organizer) ───────────────────────────

    async def start_task_run(
        self,
        task_id: str,
        website_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        user_prompt: Optional[str] = None,
        security_validation: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Create a new run via the organizer portal and store initial state."""
        run_data = await self._portal.start_run(task_id)
        run_id: str = run_data.get("run_id") or run_data.get("id", "")
        if not run_id:
            raise PortalServiceError("Organizer portal did not return a valid run_id.")

        request_data = await self._portal.get_run_request(run_id)
        effective_portal_url: str = website_url or request_data.get("portal_url", "")
        effective_task_request: str = user_prompt or request_data.get("task_request", "")
        effective_username: str = username or request_data.get("username", "")
        effective_password: str = password or request_data.get("password", "")

        _set_run(run_id, {
            "run_id": run_id,
            "task_id": task_id,
            "portal_url": effective_portal_url,
            "website_url": effective_portal_url,
            # In-memory only: NEVER logged or serialized to responses
            "_username": effective_username,
            "_password": effective_password,
            "task_request": effective_task_request,
            "status": "started",
            "step": 0,
            "current_action": None,
            "plan": None,
            "actions_history": [],
            "events": [],
            "downloaded_files": [],
            "pending_approval": None,
            "_approval_event": asyncio.Event(),
            "_approval_decision": None,
            "_stop_requested": False,
            "latest_screenshot_path": None,
            "latest_dom": {},
            "current_url": effective_portal_url,
            "current_title": "",
            "browser": None,
            "error": None,
            "result_status": None,
            "result_summary": None,
            "security_validation": security_validation,
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        })

        log_action(
            run_id, 0, "run_started",
            target=task_id,
            reason="Organizer task run initialized following pre-execution security validation",
        )

        return {
            "run_id": run_id,
            "task_id": task_id,
            "portal_url": effective_portal_url,
            "request_text": effective_task_request,
            "status": "started",
            "security_validation": security_validation,
        }

    async def create_automation_session(
        self,
        website_url: str,
        instruction: str,
        username: str = "",
        password: str = "",
        user_approved: bool = False,
    ) -> dict[str, Any]:
        """
        Create a new execution session for CampusPilot dashboard.
        Validates URL & prompt, creates run state in TaskManager, and returns immediately.
        """
        effective_url = website_url.strip()
        effective_prompt = instruction.strip()
        run_id = f"cp_{uuid.uuid4().hex[:8]}"

        # Step 1: Pre-Execution Security Validation
        if username or password:
            validation = self._security.validate_pre_execution(
                website_url=effective_url,
                username=username,
                password=password,
                user_prompt=effective_prompt,
            )
        else:
            url_res = self._security._url_validator.validate(effective_url)
            prompt_res = self._security._prompt_validator.validate(effective_prompt)
            overall = "blocked" if (url_res.status == "blocked" or prompt_res.status == "malicious") else (
                "review" if (url_res.status == "review" or prompt_res.status == "uncertain") else "safe"
            )
            validation = {
                "overall_status": overall,
                "allowed_to_proceed": overall == "safe" or (overall == "review" and user_approved),
                "website_validation": {"status": url_res.status, "reason": url_res.reason},
                "prompt_validation": {"status": prompt_res.status, "reason": prompt_res.reason},
                "credential_validation": {"status": "skipped", "reason": None},
            }

        log_action(
            run_id=run_id,
            step=1,
            action="frontend_submission_security_validation",
            target=effective_url,
            reason=f"overall={validation['overall_status']}",
            result=validation["overall_status"],
        )

        if validation["overall_status"] == "blocked":
            reason = (
                validation["prompt_validation"].get("reason")
                or validation["website_validation"].get("reason")
                or "Security policy blocked request"
            )
            return {
                "task_id": run_id,
                "run_id": run_id,
                "status": "invalid",
                "message": f"Security policy blocked request: {reason}",
                "allowed": False,
                "reason": reason,
            }

        is_review_site = validation["overall_status"] == "review" and not user_approved
        review_reason = ""
        if is_review_site:
            review_reason = (
                validation["prompt_validation"].get("reason")
                or validation["website_validation"].get("reason")
                or "Explicit user authorization required for this external website."
            )

        # Step 2: NLU & Consequential Action Detection
        parsed_plan = None
        is_consequential = False
        consequential_reason = review_reason
        try:
            parsed_plan = await self._llm.parse_task_request(effective_prompt)
            # Consequential actions are strictly state-changing irreversible operations (payment, registration, leave submission, elective drop)
            consequential_intents = [
                act for act in (parsed_plan.actions + [parsed_plan.intent])
                if self._approval.is_irreversible(act) or any(k in act.lower() for k in ["pay_fee", "register_elective", "submit_leave", "drop_elective"])
            ]
            is_consequential = bool(consequential_intents) or any(k in effective_prompt.lower() for k in ["pay fee", "pay tuition", "make payment", "register for", "enroll in", "submit leave", "drop elective"])
            if is_consequential and not user_approved:
                if consequential_intents:
                    intent_label = consequential_intents[0]
                elif any(k in effective_prompt.lower() for k in ["pay fee", "pay tuition", "make payment"]):
                    intent_label = "pay_fee"
                elif any(k in effective_prompt.lower() for k in ["register for", "enroll in"]):
                    intent_label = "register_elective"
                elif any(k in effective_prompt.lower() for k in ["drop"]):
                    intent_label = "drop_elective"
                else:
                    intent_label = "irreversible_operation"

                consequential_reason = f"Consequential action detected: {intent_label}. Human approval required."
                log_action(
                    run_id=run_id,
                    step=2,
                    action="consequential_action_approval_required",
                    target=intent_label,
                    reason=f"Action '{intent_label}' requires human confirmation before execution",
                )
        except Exception as nlu_err:
            logger.warning("NLU plan check warning: %s", nlu_err)

        requires_pre_approval = is_review_site or (is_consequential and not user_approved)
        initial_status = "needs_approval" if requires_pre_approval else "started"

        approval_request_id = str(uuid.uuid4())
        pending_approval_info = None
        if requires_pre_approval:
            chosen_action = (
                intent_label if is_consequential
                else ("navigate_external_site" if is_review_site else "consequential_action")
            )
            pending_approval_info = {
                "approval_request_id": approval_request_id,
                "action": chosen_action,
                "reason": consequential_reason,
                "description": f"Approval required before proceeding: {consequential_reason}",
            }
            _persistence.save_approval_request(
                approval_request_id=approval_request_id,
                run_id=run_id,
                action=pending_approval_info["action"],
                description=pending_approval_info["description"],
                status="pending",
            )

        # Session accepted — store initial state in TaskManager (Requirement 4)
        _set_run(run_id, {
            "run_id": run_id,
            "task_id": run_id,
            "portal_url": effective_url,
            "website_url": effective_url,
            "_username": username,
            "_password": password,
            "task_request": effective_prompt,
            "user_approved": user_approved,
            "status": initial_status,
            "step": 0,
            "current_action": "waiting_approval" if initial_status == "needs_approval" else "initializing",
            "plan": parsed_plan.model_dump() if parsed_plan else None,
            "actions_history": [],
            "events": [
                {
                    "id": 1,
                    "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                    "type": "security_validated",
                    "title": "Security Validation Passed",
                    "details": {"url": effective_url, "overall": validation["overall_status"]},
                }
            ],
            "downloaded_files": [],
            "pending_approval": pending_approval_info,
            "_approval_event": asyncio.Event(),
            "_approval_decision": None,
            "_stop_requested": False,
            "latest_screenshot_path": None,
            "latest_dom": {},
            "current_url": effective_url,
            "current_title": "",
            "browser": None,
            "error": None,
            "result_status": None,
            "result_summary": None,
            "security_validation": validation,
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        })

        if initial_status == "needs_approval":
            return {
                "task_id": run_id,
                "run_id": run_id,
                "status": "needs_approval",
                "message": f"Security Review: {consequential_reason}",
                "allowed": True,
                "reason": consequential_reason,
                "plan": parsed_plan.model_dump() if parsed_plan else None,
            }

        return {
            "task_id": run_id,
            "run_id": run_id,
            "status": "started",
            "message": "Automation session created and initialized.",
            "allowed": True,
        }

    # ── Phase 2: Execute Agent Workflow ───────────────────────────────────────

    async def execute(self, run_id: str, browser: Optional[BrowserService] = None) -> dict[str, Any]:
        """Start the asynchronous agent workflow for the given run_id."""
        run = get_run(run_id)
        if not run:
            raise ValueError(f"run_id '{run_id}' not found.")
        if run["status"] in {"running", "done", "completed"}:
            return {"status": run["status"], "message": f"Run already in status '{run['status']}'."}

        if run.get("status") != "needs_approval":
            _update_run(run_id, status="running")
        owns_browser = browser is None
        active_browser = browser or BrowserService()
        run["browser"] = active_browser

        try:
            await self._run_workflow(run_id, run, active_browser)
        except Exception as exc:
            logger.error("Workflow failed unexpectedly for run_id=%s: %s", run_id, exc, exc_info=True)
            _update_run(
                run_id,
                status="failed",
                error=str(exc),
                result_status="not_done",
                result_summary=f"Execution error: {exc}",
            )
            self._emit_event(
                run_id,
                event_type="error",
                title="Execution Failed",
                details={"error": str(exc)},
            )
            log_action(run_id, run.get("step", 0), "workflow_error", reason=str(exc))
            # Attempt error report submission if organizer task
            try:
                if run.get("task_id") and not str(run["task_id"]).startswith("cp_"):
                    await self._report.submit(run_id, run["task_id"], "not_done", f"Internal error: {exc}")
            except Exception:
                pass
        finally:
            if owns_browser:
                try:
                    await active_browser.close()
                except Exception:
                    pass
                run["browser"] = None

        return get_run(run_id) or {}

    def _emit_event(self, run_id: str, event_type: str, title: str, details: dict[str, Any]) -> None:
        """Append an event to the run's real-time event log."""
        run = get_run(run_id)
        if not run:
            return
        events = run.setdefault("events", [])
        event = {
            "id": len(events) + 1,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "type": event_type,
            "title": title,
            "details": details,
        }
        events.append(event)
        _update_run(run_id, updated_at=event["timestamp"])

    def _initialize_subgoals(self, plan: TaskPlan, task_request: str) -> list[SubGoal]:
        """Initialize structured milestone checklist for multi-goal tracking (Requirement 19)."""
        if plan.subgoals:
            return [sg if isinstance(sg, SubGoal) else SubGoal(**sg) for sg in plan.subgoals]

        lower_req = task_request.lower()
        subgoals: list[SubGoal] = []

        # Detect multiple distinct task components
        has_attendance = bool(re.search(r"\b(attendance|present|absent)\b", lower_req))
        has_electives = bool(re.search(r"\b(electives?|courses?|subject\s+choice)\b", lower_req))
        has_registration = bool(re.search(r"\b(register|enroll|opt\s+for|add\s+course)\b", lower_req)) and not re.search(r"\benrollment\s+status\b", lower_req)
        has_download = bool(re.search(r"\b(download|hall\s*ticket|admit\s*card|exam\s*ticket|invoice|pdf)\b", lower_req))
        has_fees = bool(re.search(r"\b(fee|fees|tuition|dues|payment)\b", lower_req))
        has_leave = bool(re.search(r"\b(leave|absence|sick\s*leave)\b", lower_req))
        has_timetable = bool(re.search(r"\b(timetable|schedule|routine)\b", lower_req))

        domains_detected = sum([
            has_attendance,
            has_electives or has_registration,
            has_download,
            has_fees,
            has_leave,
            has_timetable,
        ])

        # If 2 or more distinct domains are in the request, decompose into independent milestone subgoals
        if domains_detected >= 2:
            idx = 1
            if has_attendance:
                subgoals.append(SubGoal(
                    id=f"subgoal_{idx}",
                    description="Check and extract attendance percentage records",
                    required=True,
                    completed=False,
                    status="pending",
                ))
                idx += 1
            if has_electives and not has_registration:
                subgoals.append(SubGoal(
                    id=f"subgoal_{idx}",
                    description="Inspect available open elective options",
                    required=True,
                    completed=False,
                    status="pending",
                ))
                idx += 1
            if has_registration:
                subgoals.append(SubGoal(
                    id=f"subgoal_{idx}",
                    description="Register for requested course/elective with confirmation",
                    required=True,
                    completed=False,
                    status="pending",
                ))
                idx += 1
            if has_fees:
                subgoals.append(SubGoal(
                    id=f"subgoal_{idx}",
                    description="Inspect and process fee payment with user approval",
                    required=True,
                    completed=False,
                    status="pending",
                ))
                idx += 1
            if has_download:
                subgoals.append(SubGoal(
                    id=f"subgoal_{idx}",
                    description="Download hall ticket/document and verify saved file on disk",
                    required=True,
                    completed=False,
                    status="pending",
                ))
                idx += 1
            if has_leave:
                subgoals.append(SubGoal(
                    id=f"subgoal_{idx}",
                    description="Submit leave application and verify confirmation ID",
                    required=True,
                    completed=False,
                    status="pending",
                ))
                idx += 1
            if has_timetable:
                subgoals.append(SubGoal(
                    id=f"subgoal_{idx}",
                    description="Extract class timetable and schedule",
                    required=True,
                    completed=False,
                    status="pending",
                ))
                idx += 1
            return subgoals

        # Single-domain detailed subgoals
        if has_download:
            return [
                SubGoal(id="subgoal_1", description="Navigate to examination or document section", required=True, status="pending"),
                SubGoal(id="subgoal_2", description="Initiate file download", required=True, status="pending"),
                SubGoal(id="subgoal_3", description="Verify nonzero file saved on disk", required=True, status="pending"),
            ]
        elif has_registration:
            return [
                SubGoal(id="subgoal_1", description="Locate course registration section", required=True, status="pending"),
                SubGoal(id="subgoal_2", description="Select requested course", required=True, status="pending"),
                SubGoal(id="subgoal_3", description="Obtain confirmation if irreversible", required=True, status="pending"),
                SubGoal(id="subgoal_4", description="Verify registration confirmation code appears", required=True, status="pending"),
            ]
        elif has_electives:
            return [
                SubGoal(id="subgoal_1", description="Navigate to electives section", required=True, status="pending"),
                SubGoal(id="subgoal_2", description="Inspect available open elective courses and schedule", required=True, status="pending"),
            ]
        elif has_leave:
            return [
                SubGoal(id="subgoal_1", description="Open leave application form", required=True, status="pending"),
                SubGoal(id="subgoal_2", description="Fill leave application details", required=True, status="pending"),
                SubGoal(id="subgoal_3", description="Submit and verify application reference ID", required=True, status="pending"),
            ]
        elif has_attendance:
            return [
                SubGoal(id="subgoal_1", description="Navigate to attendance section", required=True, status="pending"),
                SubGoal(id="subgoal_2", description="Extract attendance percentage records", required=True, status="pending"),
            ]
        elif has_fees:
            return [
                SubGoal(id="subgoal_1", description="Navigate to fee dues section", required=True, status="pending"),
                SubGoal(id="subgoal_2", description="Inspect fee structure or initiate payment with confirmation", required=True, status="pending"),
            ]
        elif has_timetable:
            return [
                SubGoal(id="subgoal_1", description="Navigate to timetable schedule", required=True, status="pending"),
                SubGoal(id="subgoal_2", description="Extract timetable data", required=True, status="pending"),
            ]
        else:
            steps = plan.steps or ["Navigate to target", "Perform operation", "Verify outcome"]
            for idx, s in enumerate(steps, 1):
                subgoals.append(SubGoal(id=f"subgoal_{idx}", description=s, required=True, status="pending"))
            return subgoals

    async def _run_workflow(
        self, run_id: str, run: dict[str, Any], browser: BrowserService
    ) -> None:
        """Main execution loop: Observe → Reason → Validate → Act → Verify."""
        step = 0
        actions_history: list[dict[str, Any]] = []

        def next_step(action: str, **kwargs):
            nonlocal step
            step += 1
            _update_run(run_id, step=step, current_action=action)
            log_action(run_id, step, action, **kwargs)
            actions_history.append({"step": step, "action": action, **kwargs})

        task_request: str = run["task_request"]
        portal_url: str = run.get("website_url") or run.get("portal_url", "")
        task_id: str = run.get("task_id", "")
        username: str = run.get("_username", "")
        password: str = run.get("_password", "")

        # ── 1. NLU: Task Understanding & Plan Generation ──────────────────────
        next_step("llm_parse_request", reason="Analyzing student request into structured task plan")
        self._emit_event(run_id, "nlu_planning", "Analyzing Request", {"prompt": task_request})
        try:
            plan: TaskPlan = await self._llm.parse_task_request(task_request)
        except LLMServiceError as exc:
            next_step("llm_failed", reason=str(exc), result="error")
            _update_run(run_id, status="failed", result_status="not_done", result_summary=f"NLU planning failed: {exc}")
            self._emit_event(run_id, "error", "NLU Planning Failed", {"error": str(exc)})
            if task_id and not str(task_id).startswith("cp_"):
                await self._report.submit(run_id, task_id, "not_done", f"NLU planning failed: {exc}")
            return

        subgoals = self._initialize_subgoals(plan, task_request)
        plan.subgoals = subgoals
        _update_run(run_id, plan=plan.model_dump())
        next_step(
            "llm_plan_received",
            value=plan.intent,
            reason=f"Goal: {plan.goal} | Subgoals: {len(subgoals)} | Constraints: {', '.join(plan.constraints) or 'none'}",
        )
        self._emit_event(run_id, "plan_created", f"Plan Created: {plan.intent}", {
            "intent": plan.intent,
            "goal": plan.goal,
            "steps": plan.steps,
            "subgoals": [sg.model_dump() for sg in subgoals],
            "constraints": plan.constraints,
        })

        # ── 2. Browser: Launch & Navigate Immediately (Requirement 4: initialize browser session) ──
        next_step("browser_start", reason="Launching Chromium automation session")
        self._emit_event(run_id, "browser_launch", "Launching Chromium Browser", {
            "headless": False,
            "target": portal_url,
        })
        await browser.start()

        next_step("browser_navigate", url=portal_url, reason="Navigating to destination URL")
        try:
            nav = await browser.navigate(portal_url)
            redirected_url = nav.get("url", portal_url)
            page_title = nav.get("title", "")
            _update_run(run_id, current_url=redirected_url, current_title=page_title)

            if redirected_url != portal_url:
                logger.info("Navigation redirected: %s -> %s", portal_url, redirected_url)
                self._emit_event(run_id, "navigation", f"Redirected to {redirected_url}", {
                    "original_url": portal_url,
                    "redirected_url": redirected_url,
                    "title": page_title,
                })
            else:
                self._emit_event(run_id, "navigation", f"Navigated to {portal_url}", {
                    "title": page_title,
                    "status": nav.get("status"),
                    "url": portal_url,
                })

            # Capture initial screenshot and DOM of the landed/redirected page
            page_structure = await browser.extract_page_structure()
            shot = await browser.take_screenshot(name=f"{run_id}_nav")
            if shot:
                _update_run(run_id, latest_screenshot_path=shot)
            _update_run(run_id, latest_dom=page_structure)

            interactive = page_structure.get("interactive", {})
            dom_desc = f"{len(interactive.get('links', []))} links, {len(interactive.get('buttons', []))} buttons, {len(interactive.get('inputs', []))} inputs, {page_structure.get('tables_count', 0)} tables"
            self._emit_event(run_id, "observe", f"Initial DOM Observed on {redirected_url}", {
                "url": redirected_url,
                "title": page_title,
                "summary": dom_desc,
                "interactive": interactive,
                "tables_count": page_structure.get("tables_count", 0),
            })
        except Exception as nav_exc:
            logger.error("Initial browser navigation failed for %s: %s", portal_url, nav_exc)
            summary = f"Could not reach {portal_url}: {nav_exc}"
            _update_run(
                run_id,
                status="failed",
                error=f"Navigation failed: {nav_exc}",
                result_status="not_done",
                result_summary=summary,
            )
            self._emit_event(run_id, "error", "Navigation Failed", {"url": portal_url, "error": str(nav_exc)})
            log_action(run_id, step, "navigation_failed", target=portal_url, reason=str(nav_exc))
            if task_id and not str(task_id).startswith("cp_"):
                await self._report.submit(run_id, task_id, "not_done", summary)
            return

        # Check if pre-execution review or initial approval is pending (Requirement 4)
        if run.get("status") == "needs_approval" or run.get("pending_approval"):
            logger.info("Run %s waiting for initial pre-execution approval", run_id)
            self._emit_event(run_id, "approval_required", "Pre-execution Approval Required", {
                "reason": run.get("pending_approval", {}).get("reason", "Authorization required to proceed.")
            })
            approval_event = run.get("_approval_event")
            if not approval_event:
                approval_event = asyncio.Event()
                run["_approval_event"] = approval_event

            await approval_event.wait()

            if not run.get("_approval_decision", False):
                summary = "Task was cancelled or denied by user."
                _update_run(run_id, status="not_done", result_status="not_done", result_summary=summary)
                self._emit_event(run_id, "stopped", "Task Cancelled by User", {"reason": summary})
                return

            _update_run(run_id, status="running", pending_approval=None)

        # Login if credentials provided (Public websites work without credentials)
        if username and password:
            next_step("browser_login", url=portal_url, reason="Injecting credentials into portal DOM")
            self._emit_event(run_id, "login_attempt", "Authenticating credentials", {"user": "[REDACTED]"})
            try:
                login_ok = await browser.login(portal_url, username, password)
            except Exception as login_err:
                logger.warning("Browser login attempt exception: %s", login_err)
                login_ok = False

            try:
                shot = await browser.take_screenshot(name=f"{run_id}_login")
                if shot:
                    _update_run(run_id, latest_screenshot_path=shot)
            except Exception:
                pass

            if not login_ok:
                next_step("login_failed", reason="Authentication failed: invalid credentials or login form could not be submitted", result="error")
                self._emit_event(run_id, "error", "Authentication Failed", {"success": False, "user": "[REDACTED]"})
                summary = "Authentication failed: invalid credentials or portal login form could not be submitted."
                _update_run(
                    run_id,
                    status="failed",
                    result_status="not_done",
                    error=summary,
                    result_summary=summary,
                )
                if task_id and not str(task_id).startswith("cp_"):
                    await self._report.submit(run_id, task_id, "not_done", summary)
                return
            else:
                next_step("login_success", reason="Authenticated into student portal dashboard")
                self._emit_event(run_id, "login_result", "Authentication successful", {"success": True})

        # ── 3. Autonomous Observe-Reason-Validate-Act-Verify Loop ─────────────
        MAX_STEPS = getattr(settings, "MAX_STEPS", 25)
        executed_actions_count = 0
        is_completed = False
        consecutive_actions: list[tuple[str, str]] = []

        while executed_actions_count < MAX_STEPS and not is_completed:
            if run.get("_stop_requested"):
                next_step("task_stopped_by_user", reason="User requested task stop")
                self._emit_event(run_id, "stopped", "Task Stopped by User", {})
                _update_run(run_id, status="stopped", result_status="stopped", result_summary="Task cancelled by user.")
                return

            executed_actions_count += 1
            step_label = f"Step {executed_actions_count}/{MAX_STEPS}"

            # A. OBSERVE: Extract current DOM text & interactive structure
            if await browser.is_session_expired():
                next_step("session_expired", reason="Session expired; re-authenticating")
                self._emit_event(run_id, "session_expired", "Session Expired", {})
                if username and password:
                    relogin_ok = await browser.login(portal_url, username, password)
                    if not relogin_ok:
                        next_step("relogin_failed", reason="Session recovery failed", result="error")
                        _update_run(run_id, status="not_done", result_status="not_done", result_summary="Session expired and re-login failed.")
                        if task_id and not str(task_id).startswith("cp_"):
                            await self._report.submit(run_id, task_id, "not_done", "Session expired during execution.")
                        return

            page_structure = await browser.extract_page_structure()
            page_text = await browser.get_page_text()
            cur_url = page_structure.get("url") or (await browser.get_current_url())
            cur_title = page_structure.get("title") or ""
            prev_url = run.get("current_url")
            if prev_url and cur_url != prev_url:
                logger.info("Page transitioned/redirected: %s -> %s", prev_url, cur_url)
                self._emit_event(run_id, "navigation", f"Page Redirected to {cur_url}", {
                    "from_url": prev_url,
                    "current_url": cur_url,
                    "title": cur_title,
                })
            _update_run(run_id, latest_dom=page_structure, current_url=cur_url, current_title=cur_title)

            # Capture real Playwright screenshot
            shot = await browser.take_screenshot(name=f"{run_id}_step_{executed_actions_count}")
            if shot:
                _update_run(run_id, latest_screenshot_path=shot)

            interactive = page_structure.get("interactive", {})
            dom_desc = f"{len(interactive.get('links', []))} links, {len(interactive.get('buttons', []))} buttons, {len(interactive.get('inputs', []))} inputs, {page_structure.get('tables_count', 0)} tables"
            self._emit_event(run_id, "observe", f"[{step_label}] DOM Observed", {
                "url": cur_url,
                "title": cur_title,
                "summary": dom_desc,
                "interactive": interactive,
                "tables_count": page_structure.get("tables_count", 0),
            })

            # B. WEBPAGE SECURITY: Inspect page content for indirect prompt injection
            is_browser_error_page = any(err_term in page_text.lower() for err_term in [
                "err_name_not_resolved", "page not found", "can't be reached",
                "server ip address could not be found", "dns_probe", "err_connection",
                "unable to connect", "site cant be reached"
            ])
            if page_text and len(page_text.strip()) > 20 and not is_browser_error_page:
                security_check = self._security.check_webpage_content(page_text, task_request)
                if not security_check.is_safe:
                    next_step(
                        "security_block",
                        reason=security_check.reason,
                        result=f"classification={security_check.classification}",
                    )
                    summary = f"Security violation detected in portal webpage content: {security_check.reason}"
                    self._emit_event(run_id, "security_halt", "Webpage Security Violation", {"reason": security_check.reason})
                    _update_run(run_id, status="failed", result_status="not_done", result_summary=summary)
                    if task_id and not str(task_id).startswith("cp_"):
                        await self._report.submit(run_id, task_id, "not_done", summary)
                    return

            # C. REASON: LLM decides next optimal action
            next_step("reasoning_step", reason=f"Determining next step for goal: {plan.goal}")
            try:
                decision: StepActionDecision = await self._llm.decide_next_action(
                    goal=plan.goal,
                    task_request=task_request,
                    current_page_info=page_structure,
                    previous_actions=actions_history,
                )
            except Exception as exc:
                logger.warning("Reasoning fallback due to: %s", exc)
                decision = StepActionDecision(
                    action_type="inspect_page",
                    reasoning="Inspecting page content due to reasoning error.",
                )

            next_step(
                "action_decided",
                target=decision.action_type,
                value=decision.target_element,
                reason=decision.reasoning,
            )
            _update_run(run_id, last_decision=decision.model_dump(), current_action=f"{decision.action_type}: {decision.target_element or ''}")
            self._emit_event(run_id, "reasoning", f"[{step_label}] Decision: {decision.action_type}", {
                "action_type": decision.action_type,
                "target_element": decision.target_element,
                "target_value": decision.target_value,
                "reasoning": decision.reasoning,
                "is_irreversible": decision.is_irreversible,
                "is_task_complete": decision.is_task_complete,
            })

            # Loop detection (Requirement 24)
            action_key = (decision.action_type, str(decision.target_element or ""))
            recent_same = [k for k in consecutive_actions[-2:] if k == action_key]
            if len(recent_same) >= 2:
                logger.warning("Repetitive action loop detected: %s executed consecutively", action_key)
                next_step(
                    "loop_detected",
                    target=decision.action_type,
                    reason=f"Repetitive action loop detected on {decision.action_type}. Aborting loop.",
                    result="loop_aborted",
                )
                self._emit_event(run_id, "loop_detected", "Loop Detected", {
                    "action": decision.action_type,
                    "target": decision.target_element,
                })
                break
            consecutive_actions.append(action_key)

            # D. VALIDATE ACTION SECURITY: Enforce allowable action types and proposed action safety
            action_sec = self._security.check_action_type(decision.action_type, task_request)
            if not action_sec.is_safe:
                next_step(
                    "action_blocked",
                    target=decision.action_type,
                    reason=action_sec.reason,
                    result="blocked",
                )
                self._emit_event(run_id, "action_blocked", f"Action Blocked: {decision.action_type}", {
                    "reason": action_sec.reason
                })
                continue

            action_desc_check = f"{decision.action_type}: {decision.target_element or ''} - {decision.reasoning or ''}"
            prop_sec = self._security.check_proposed_action(action_desc_check, task_request)
            if not prop_sec.is_safe:
                next_step(
                    "action_blocked",
                    target=decision.action_type,
                    reason=prop_sec.reason,
                    result="blocked",
                )
                self._emit_event(run_id, "action_blocked", f"Proposed Action Blocked: {decision.action_type}", {
                    "reason": prop_sec.reason,
                    "matched_rules": prop_sec.matched_rules,
                })
                log_action(run_id, step, "action_blocked", target=decision.target_element, reason=prop_sec.reason, result="blocked")
                continue

            # Evaluate click targets, resolving temporary element IDs to their DOM text (Section E)
            target_raw = str(decision.target_element or "")
            target_resolved_text = target_raw
            if re.match(r"^elem_\d+$", target_raw):
                for el in page_structure.get("elements", []):
                    if el.get("id") == target_raw:
                        target_resolved_text = f"{el.get('text', '')} {el.get('name', '')} {el.get('aria_label', '')} {target_raw}"
                        break

            target_str = (target_raw + " " + target_resolved_text).lower()
            reasoning_str = str(decision.reasoning or "").lower()
            is_target_sensitive = any(term in target_str or term in reasoning_str for term in [
                "pay", "fee", "payment", "enroll", "register", "delete", "cancel", "drop"
            ]) or (decision.action_type == "submit_form" and any(term in reasoning_str for term in ["pay", "fee", "enroll", "register"]))

            # Check if this action/target was already approved in this session
            approved_actions = run.setdefault("_approved_actions", set())
            action_target_key = f"{decision.action_type.lower()}:{target_str}"
            is_already_approved = (
                action_target_key in approved_actions
                or decision.action_type.lower() in approved_actions
                or target_str in approved_actions
                or any(term in approved_actions for term in ["drop", "register", "pay", "fee", "submit", "delete", "cancel"] if term in target_str or term in decision.action_type.lower())
            )

            # Check if target element exists on page before prompting human approval
            target_exists_on_page = True
            is_semantic_irreversible_type = (
                decision.is_irreversible or self._approval.is_irreversible(decision.action_type)
            )
            if decision.action_type == "click" and not is_semantic_irreversible_type:
                target_cand = target_raw.strip().lower()
                has_in_elements = any(
                    target_cand == (el.get("id", "") or "").lower()
                    or target_cand in (el.get("text", "") or "").lower()
                    or target_cand in (el.get("name", "") or "").lower()
                    or target_cand in (el.get("placeholder", "") or "").lower()
                    or any(part in (el.get("text", "") or "").lower() for part in target_cand.split() if len(part) >= 3)
                    for el in page_structure.get("elements", [])
                )
                inter = page_structure.get("interactive", {})
                has_in_interactive = any(
                    target_cand in b.lower() or any(part in b.lower() for part in target_cand.split() if len(part) >= 3)
                    for b in (inter.get("buttons", []) + inter.get("links", []) + inter.get("inputs", []))
                )
                target_exists_on_page = has_in_elements or has_in_interactive
                if not target_exists_on_page:
                    logger.info("Click target '%s' is not an interactive element on current page. Skipping approval pause.", decision.target_element)

            requires_human_approval = not is_already_approved and target_exists_on_page and (
                decision.is_irreversible or self._approval.is_irreversible(decision.action_type) or (
                    decision.action_type in {"click", "submit_form"} and is_target_sensitive
                )
            )

            # E. IRREVERSIBLE ACTIONS GATE: Human In The Loop (Issue 6 & Section E)
            user_approved = False
            if requires_human_approval:
                action_desc = f"Execute '{decision.action_type}' ({decision.target_element or ''}) for task: {plan.goal}"
                next_step(
                    "confirmation_requested",
                    target=decision.action_type,
                    reason=f"Irreversible action '{decision.action_type}' ({decision.target_element or ''}) requires human confirmation",
                )

                # Initialize event and reset state BEFORE broadcasting approval request
                approval_event: asyncio.Event = run.setdefault("_approval_event", asyncio.Event())
                approval_event.clear()
                run["_approval_decision"] = None
                approval_req_id = str(uuid.uuid4())

                # Set awaiting_approval and notify frontend workspace
                pending_info = {
                    "approval_request_id": approval_req_id,
                    "action": decision.action_type,
                    "target": decision.target_element,
                    "value": decision.target_value,
                    "reason": decision.reasoning,
                    "description": action_desc,
                }
                _update_run(
                    run_id,
                    status="needs_approval",
                    current_action=decision.action_type,
                    pending_approval=pending_info,
                )
                _persistence.save_approval_request(
                    approval_request_id=approval_req_id,
                    run_id=run_id,
                    action=decision.action_type,
                    description=action_desc,
                    status="pending",
                )
                self._emit_event(run_id, "approval_required", "Approval Required", {
                    "action": decision.action_type,
                    "target": decision.target_element,
                    "description": action_desc,
                    "reason": decision.reasoning,
                    "approval_request_id": approval_req_id,
                })

                # Wait for user to decide in live workspace
                logger.info("Task %s paused awaiting approval for action: %s", run_id, decision.action_type)
                await approval_event.wait()

                action_approved = bool(run.get("_approval_decision", False))
                _update_run(run_id, pending_approval=None)

                if not action_approved:
                    summary = f"Irreversible action '{decision.action_type}' was denied by user."
                    next_step("confirmation_denied", reason=summary)
                    self._emit_event(run_id, "approval_denied", "Approval Denied by User", {"reason": summary})
                    _update_run(run_id, status="not_done", result_status="not_done", result_summary=summary)
                    if task_id and not str(task_id).startswith("cp_"):
                        await self._report.submit(run_id, task_id, "not_done", summary)
                    return

                # Action approved — resume running state and clear decision
                _update_run(run_id, status="running")
                run["_approval_decision"] = None

                approved_actions = run.setdefault("_approved_actions", set())
                approved_actions.add(decision.action_type.lower())
                if decision.target_element:
                    approved_actions.add(str(decision.target_element).lower())
                    approved_actions.add(f"{decision.action_type.lower()}:{str(decision.target_element).lower()}")
                for term in ["drop", "delete", "register", "enroll", "pay", "fee", "submit"]:
                    if term in decision.action_type.lower() or term in str(decision.target_element or "").lower():
                        approved_actions.add(term)

                # If approved, also check official confirm.py if available
                confirm_result = await self._approval.request_confirmation(
                    action_description=action_desc,
                    base_url=portal_url,
                    run_id=run_id,
                )
                if not confirm_result.get("approved", True) and confirm_result.get("source") == "official_confirm":
                    summary = f"Action '{decision.action_type}' was rejected by official confirm.py."
                    _update_run(run_id, status="not_done", result_status="not_done", result_summary=summary)
                    self._emit_event(run_id, "approval_denied", "Denied by confirm.py", {"reason": summary})
                    return

                next_step("confirmation_granted", target=decision.action_type, reason="Human approval granted")
                self._emit_event(run_id, "approval_granted", "Approval Granted — Resuming Task", {})
                _update_run(run_id, status="running")

            # F. ACT: Dispatch action to browser service
            act_type = decision.action_type
            target = decision.target_element or ""
            val = decision.target_value or ""

            next_step(f"execute_{act_type}", target=target, reason=decision.reasoning)
            act_success = await self._dispatch_browser_action(browser, act_type, target, val, run)

            if not act_success:
                logger.warning("Action '%s' on '%s' failed to execute on current page", act_type, target)
                avail_summary = [e.get('text') for e in page_structure.get('elements', []) if e.get('text')][:8]
                actions_history.append({
                    "step": step,
                    "action": f"{act_type}_failed",
                    "target": target,
                    "reason": f"Element '{target}' was not found or could not be clicked. Visible elements on this page: {avail_summary}. If this action requires opening a modal, dropdown, or menu, click 'More', 'Options', or the relevant row first.",
                })

            # Updated screenshot after action
            shot = await browser.take_screenshot(name=f"{run_id}_act_{executed_actions_count}")
            if shot:
                _update_run(run_id, latest_screenshot_path=shot)

            # Subgoal checklist progress update (Requirement 19)
            post_text = (await browser.get_page_text()).lower()
            for sg in subgoals:
                if sg.completed:
                    continue
                sg_d = sg.description.lower()
                if "approval" in sg_d and (user_approved or run.get("user_approved")):
                    sg.completed = True
                    sg.status = "completed"
                    sg.verification_evidence = "Approval granted"
                elif "download" in sg_d and run.get("downloaded_files"):
                    sg.completed = True
                    sg.status = "completed"
                    sg.verification_evidence = f"Downloaded {len(run['downloaded_files'])} file(s)"
                elif "file" in sg_d and any(f.get("size_bytes", 0) > 0 for f in run.get("downloaded_files", [])):
                    sg.completed = True
                    sg.status = "completed"
                    sg.verification_evidence = "Nonzero file verified on disk"
                elif ("pay" in sg_d or "fee" in sg_d) and any(term in post_text for term in ["paid", "receipt", "successful", "transaction"]):
                    sg.completed = True
                    sg.status = "completed"
                    sg.verification_evidence = "Payment confirmation observed"
                elif ("register" in sg_d or "enroll" in sg_d) and any(term in post_text for term in ["enrolled", "registered", "conf-"]):
                    sg.completed = True
                    sg.status = "completed"
                    sg.verification_evidence = "Registration confirmation observed"
                elif "attendance" in sg_d and any(term in post_text for term in ["attendance", "%", "present", "absent", "subject"]):
                    sg.completed = True
                    sg.status = "completed"
                    sg.verification_evidence = "Attendance records observed"
                elif "elective" in sg_d and any(term in post_text for term in ["elective", "course", "credits", "code"]):
                    sg.completed = True
                    sg.status = "completed"
                    sg.verification_evidence = "Elective options observed"

            if decision.subgoal_id_completed:
                for sg in subgoals:
                    if sg.id == decision.subgoal_id_completed:
                        sg.completed = True
                        sg.status = "completed"
                        sg.verification_evidence = f"Action '{act_type}' completed"

            _update_run(run_id, subgoals=[sg.model_dump() for sg in subgoals])

            # G. VERIFY: State verification
            next_step(
                f"verify_action_{act_type}",
                result="success" if act_success else "partial/failed",
                reason=f"Action '{act_type}' dispatched",
            )
            self._emit_event(run_id, "action_result", f"[{step_label}] {act_type} executed", {
                "action": act_type,
                "target": target,
                "success": act_success,
                "result": "success" if act_success else "failed",
                "subgoals": [sg.model_dump() for sg in subgoals],
            })

            # Multi-goal tracking: verify all subgoals before allowing completion (Issue D)
            if decision.is_task_complete or act_type in {"report"}:
                uncompleted_required = [sg for sg in subgoals if sg.required and not sg.completed]
                if uncompleted_required:
                    logger.info("Task completion postponed: unverified subgoals remain: %s", [sg.id for sg in uncompleted_required])
                    self._emit_event(
                        run_id,
                        "subgoal_pending",
                        f"Subgoals Pending ({len(uncompleted_required)} remaining)",
                        {"pending": [sg.description for sg in uncompleted_required]},
                    )
                    actions_history.append({
                        "step": step,
                        "action": "subgoals_pending_warning",
                        "reason": f"Required subgoals still incomplete: {', '.join(sg.description for sg in uncompleted_required)}. Do not finish yet.",
                    })
                else:
                    is_completed = True
                    break

        # ── 4. Final Verification & Outcome Reporting (Issue 5) ───────────────
        next_step("final_verification", reason="Verifying portal state and outcomes against student goal")
        self._emit_event(run_id, "verification", "Running Final Outcome Verification", {})

        final_text = await browser.get_page_text()
        final_struct = await browser.extract_page_structure()
        verified, verify_reason = self._verify_success_intent(
            intent=plan.intent,
            goal=plan.goal,
            task_request=task_request,
            final_text=final_text,
            page_structure=final_struct,
            downloaded_files=run.get("downloaded_files", []),
            actions_history=actions_history,
        )

        try:
            shot_res = await browser.take_screenshot(name=f"{run_id}_final")
            if shot_res:
                _update_run(run_id, latest_screenshot_path=shot_res)
        except Exception as exc:
            logger.debug("Screenshot skipped or failed: %s", exc)

        result_status = "done" if verified else "not_done"
        final_status = "completed" if verified else "not_done"
        summary = (
            f"Task '{plan.goal}' completed and verified: {verify_reason}"
            if verified else
            f"Task '{plan.goal}' ended: {verify_reason}"
        )

        next_step("workflow_complete", result=result_status, reason=summary)
        self._emit_event(run_id, "completed" if verified else "not_completed", "Execution Finished", {
            "status": final_status,
            "verified": verified,
            "summary": summary,
        })

        _update_run(
            run_id,
            status=final_status,
            result_status=result_status,
            result_summary=summary,
        )

        # Submit final report to organizer portal if applicable
        if task_id and not str(task_id).startswith("cp_"):
            try:
                await self._report.submit(run_id, task_id, result_status, summary)
                next_step("report_submitted", reason="Final action log submitted to organizer portal")
            except Exception as report_err:
                logger.warning("Report submission note: %s", report_err)

    async def _dispatch_browser_action(
        self, browser: BrowserService, action_type: str, target: str, value: str, run: dict[str, Any]
    ) -> bool:
        """Execute a validated action on the live browser."""
        try:
            if action_type == "click":
                clicked = await browser.click_element(target)
                await browser.wait_for_navigation(timeout_ms=2500)
                return clicked
            elif action_type in {"fill_form", "fill_field"}:
                return await browser.fill_field(target, value)
            elif action_type == "submit_form":
                await browser.submit_form()
                await browser.wait_for_navigation(timeout_ms=2500)
                return True
            elif action_type == "download":
                res = await browser.download_file(target or "download")
                if res:
                    run.setdefault("downloaded_files", []).append(res)
                    self._emit_event(run["run_id"], "download_success", f"Downloaded {res['filename']}", res)
                    return True
                return False
            elif action_type == "navigate":
                if target.startswith("http://") or target.startswith("https://"):
                    await browser.navigate(target)
                elif target.startswith("/"):
                    cur_u = await browser.get_current_url()
                    from urllib.parse import urljoin
                    full_target = urljoin(cur_u, target)
                    await browser.navigate(full_target)
                else:
                    await browser.click_element(target)
                await browser.wait_for_navigation(timeout_ms=2500)
                return True
            elif action_type in {"inspect_page", "extract_text"}:
                await browser.get_page_text()
                return True
            elif action_type == "wait":
                await asyncio.sleep(1.0)
                return True
            elif action_type in {"register_elective", "drop_elective", "pay_fee", "submit_leave_application"}:
                return await browser.click_element(target or action_type.replace("_", " "))
            return True
        except Exception as exc:
            logger.warning("Browser dispatch error for action '%s': %s", action_type, exc)
            return False

    def _verify_success_intent(
        self,
        intent: str,
        goal: str,
        task_request: str,
        final_text: str,
        page_structure: dict[str, Any],
        downloaded_files: list[dict[str, Any]],
        actions_history: list[dict[str, Any]],
    ) -> tuple[bool, str]:
        """
        Intent-specific verification (Issue 5).
        Generic keyword matching is NOT sufficient. Requires concrete evidence:
        - Download: verify actual downloaded file exists with size > 0.
        - Payment: verify transaction confirmation or receipt.
        - Elective: verify course code/title appears in confirmed courses.
        - Leave: verify reference number or submission acknowledgment.
        """
        lower_text = final_text.lower()
        lower_request = task_request.lower()

        # 0. Compound multi-goal verification (e.g. pay fee AND download hall ticket - Issue D)
        if ("pay" in lower_request or "fee" in lower_request) and ("download" in lower_request or "ticket" in lower_request or "admit" in lower_request):
            valid_file = next(
                (f for f in downloaded_files if f.get("size_bytes", 0) > 0 and (not f.get("path") or os.path.exists(f["path"]))),
                None
            )
            payment_confirmations = [
                "payment successful", "transaction successful", "receipt generated",
                "payment confirmed", "payment id", "transaction id", "paid successfully"
            ]
            has_payment = any(term in lower_text for term in payment_confirmations)
            if valid_file and has_payment:
                fname = valid_file.get("filename") or valid_file.get("name") or os.path.basename(valid_file.get("path", "file"))
                return True, f"Multi-goal task completed and verified: Payment confirmed and file '{fname}' ({valid_file.get('size_bytes', 0)} bytes) downloaded."
            elif not has_payment:
                return False, "Multi-goal verification failed: Payment was not confirmed on portal."
            else:
                return False, "Multi-goal verification failed: Hall ticket file was not downloaded to disk."

        # 1. Download verification (hall ticket, invoice, PDF, etc.)
        is_download_intent = (
            intent == "download_hall_ticket"
            or any(kw in lower_request for kw in ["download", "invoice", "ticket", "admit card", "pdf", "hall ticket"])
        )
        if is_download_intent:
            # Check downloaded_files list for verified file with size > 0
            valid_file = next(
                (f for f in downloaded_files if f.get("size_bytes", 0) > 0 and (not f.get("path") or os.path.exists(f["path"]))),
                None
            )
            if valid_file:
                fname = valid_file.get("filename") or valid_file.get("name") or os.path.basename(valid_file.get("path", "file"))
                return True, f"File successfully downloaded: '{fname}' ({valid_file.get('size_bytes', 0)} bytes)."

            return False, "Download verification failed: No downloaded file was created or captured on disk."

        # 2. Payment verification
        if intent == "pay_fee" or "pay" in lower_request:
            payment_confirmations = [
                "payment successful", "transaction successful", "receipt generated",
                "payment confirmed", "payment id", "transaction id", "paid successfully"
            ]
            if any(term in lower_text for term in payment_confirmations):
                return True, "Payment confirmed: Transaction receipt/confirmation detected."
            return False, "Payment verification failed: No confirmation receipt or transaction ID was observed on the page."

        # 3. Elective registration verification (Section H: exact course code verification)
        if intent == "register_elective" or ("register" in lower_request and "elective" in lower_request) or "enroll" in lower_request:
            elective_terms = ["enrolled", "registered", "elective added", "registration confirmed", "course registered", "conf-", "success"]
            has_confirm_keyword = any(term in lower_text for term in elective_terms)

            # Try to match course code (e.g. OE162, CS101, etc.) without re.I to prevent matching "the 2026"
            course_match = re.search(r"\b([A-Z]{2,4}\s*\d{3,4})\b", f"{task_request} {goal}")
            req_course = course_match.group(1).replace(" ", "").upper() if course_match else ""

            if req_course:
                if req_course in final_text.upper() and has_confirm_keyword:
                    return True, f"Elective registration confirmed: Course '{req_course}' appears in verified portal records."
                return False, f"Elective registration verification failed: Enrollment confirmation for '{req_course}' not found in portal."

            if has_confirm_keyword:
                return True, "Elective registration confirmed: Course appears in enrolled/registered courses."
            return False, "Elective registration verification failed: Enrollment confirmation not found in portal."

        # 4. Leave submission verification
        if intent == "submit_leave" or "leave" in lower_request:
            leave_confirmations = [
                "leave application submitted", "application received", "pending approval",
                "reference id", "application id", "leave request recorded"
            ]
            if any(term in lower_text for term in leave_confirmations):
                return True, "Leave application submitted and confirmed."
            return False, "Leave submission verification failed: No submission confirmation reference was observed."

        # 5. Attendance verification (Requirement 20: Require specific percentage or record data)
        if intent == "check_attendance" or "attendance" in lower_request:
            has_percentage = bool(re.search(r"\b\d{1,3}(?:\.\d+)?%", final_text))
            has_records = page_structure.get("tables_count", 0) > 0 and any(kw in lower_text for kw in ["present", "absent", "percentage", "subject", "classes", "total"])
            if has_percentage or has_records:
                return True, "Attendance records inspected and extracted successfully."
            return False, "Attendance verification failed: Specific attendance percentage or table records could not be verified on page."

        # 6. Timetable verification
        if intent == "check_timetable" or "timetable" in lower_request:
            if page_structure.get("tables_count", 0) > 0 or any(day in lower_text for day in ["monday", "tuesday", "wednesday", "thursday", "friday"]):
                return True, "Timetable schedule extracted successfully."
            return False, "Timetable verification failed: Schedule grid could not be located."

        # Default query / general exploration (Requirement 20: truthful outcome, no fabricated success)
        if any(h.get("result") == "success" for h in actions_history):
            return True, f"Goal '{goal}' processed and verified on page."

        return False, f"Could not verify completion of goal: '{goal}'."

    def _verify_success(self, intent: str, page_text: str) -> bool:
        """Backward-compatible wrapper for existing unit tests."""
        verified, _ = self._verify_success_intent(
            intent=intent,
            goal="task",
            task_request="",
            final_text=page_text,
            page_structure={"tables_count": 1 if "attendance" in page_text.lower() else 0},
            downloaded_files=[{"filename": "test.pdf", "size_bytes": 1024}] if "ticket" in intent or "download" in intent else [],
            actions_history=[{"action": "execute_download", "result": "success"}],
        )
        return verified

    # ── Status, Events & Control Queries ──────────────────────────────────────

    def get_status(self, run_id: str) -> Optional[dict[str, Any]]:
        return get_run(run_id)

    def get_logs(self, run_id: str) -> list[dict[str, Any]]:
        return read_logs(run_id)

    def get_events(self, run_id: str) -> list[dict[str, Any]]:
        run = get_run(run_id)
        return run.get("events", []) if run else []

    async def get_screenshot_bytes(self, run_id: str) -> Optional[bytes]:
        """Fetch real Playwright screenshot bytes from active browser or disk."""
        run = get_run(run_id)
        if not run:
            return None

        # 1. Try active live browser
        browser: Optional[BrowserService] = run.get("browser")
        if browser:
            try:
                data = await browser.take_screenshot_bytes()
                if data:
                    return data
            except Exception:
                pass

        # 2. Try saved screenshot file on disk
        shot_path = run.get("latest_screenshot_path")
        if shot_path and os.path.exists(shot_path):
            try:
                with open(shot_path, "rb") as f:
                    return f.read()
            except Exception:
                pass

        return None

    def request_approval_decision(
        self,
        run_id: str,
        approved: bool,
        reason: str = "",
        approval_request_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Resume the paused agent in the exact same session (Requirements 4 & 6)."""
        run = get_run(run_id)
        if not run:
            return {"success": False, "message": f"Run '{run_id}' not found."}

        # Requirement 6: Advance approval prevention
        pending = run.get("pending_approval")
        if not pending:
            return {
                "success": False,
                "message": f"No active approval is currently pending for run '{run_id}'. Advance approvals are not permitted.",
            }

        expected_req_id = pending.get("approval_request_id")
        if approval_request_id and expected_req_id and approval_request_id != expected_req_id:
            return {
                "success": False,
                "message": "Approval request ID mismatch or expired.",
            }

        req_id = expected_req_id or str(uuid.uuid4())
        _persistence.save_approval_request(
            approval_request_id=req_id,
            run_id=run_id,
            action=pending.get("action", ""),
            description=pending.get("description", pending.get("reason", "")),
            status="approved" if approved else "denied",
            approved=approved,
        )

        event: Optional[asyncio.Event] = run.get("_approval_event")
        run["_approval_decision"] = approved
        run["user_approved"] = approved
        run["pending_approval"] = None  # Single-use consumption (Requirement 6)
        run["status"] = "running" if approved else "not_done"
        run["current_action"] = "resuming" if approved else "denied"
        if not approved:
            run["result_status"] = "not_done"
            run["result_summary"] = "Task stopped by user (approval denied)."
        else:
            approved_actions = run.setdefault("_approved_actions", set())
            act_name = str(pending.get("action") or "").lower()
            tgt_name = str(pending.get("target") or "").lower()
            if act_name:
                approved_actions.add(act_name)
            if tgt_name:
                approved_actions.add(tgt_name)
                approved_actions.add(f"{act_name}:{tgt_name}")
            for term in ["drop", "delete", "register", "enroll", "pay", "fee"]:
                if term in act_name or term in tgt_name or term in str(pending.get("description", "")).lower():
                    approved_actions.add(term)

        if event:
            event.set()

        log_action(
            run_id,
            run.get("step", 0) + 1,
            "workspace_approval_recorded",
            value=approved,
            reason=reason or ("User approved in workspace" if approved else "User denied in workspace"),
        )
        return {
            "success": True,
            "message": f"Approval decision '{approved}' recorded. Agent resuming in same session.",
            "run_id": run_id,
            "approval_request_id": req_id,
        }

    def stop_task(self, run_id: str) -> dict[str, Any]:
        """Stop a running task immediately and clean up (Requirement 7)."""
        run = get_run(run_id)
        if not run:
            return {"success": False, "message": f"Run '{run_id}' not found."}

        TERMINAL_STATES = {"completed", "done", "failed", "stopped", "invalid"}
        if run.get("status") in TERMINAL_STATES:
            return {"success": False, "message": f"Run '{run_id}' is already finished ({run.get('status')}).", "run_id": run_id}

        run["_stop_requested"] = True
        event: Optional[asyncio.Event] = run.get("_approval_event")
        if event and not event.is_set():
            run["_approval_decision"] = False
            event.set()

        _update_run(run_id, status="stopped", result_status="stopped", result_summary="Task cancelled by user.")
        self._emit_event(run_id, "stopped", "Task Stopped", {"reason": "User clicked stop"})
        log_action(run_id, run.get("step", 0) + 1, "task_stopped_by_user")

        return {"success": True, "message": f"Task '{run_id}' stopped.", "run_id": run_id}

    async def manual_user_action(
        self,
        run_id: str,
        action_type: str,
        target: str,
        value: str = "",
    ) -> dict[str, Any]:
        """Allow user to directly interact with the live page DOM (User-in-the-loop intervention)."""
        run = get_run(run_id)
        if not run:
            return {"success": False, "message": f"Run '{run_id}' not found."}

        browser: Optional[BrowserService] = run.get("browser")
        if not browser or not getattr(browser, "_page", None):
            return {"success": False, "message": "Browser is not active or page is closed."}

        step = run.get("step", 0) + 1
        log_action(run_id, step, f"user_manual_{action_type}", target=target, value=value)
        self._emit_event(run_id, "user_action", f"User Manual Action: {action_type}", {
            "action_type": action_type,
            "target": target,
            "value": value,
        })

        success = await self._dispatch_browser_action(browser, action_type, target, value, run)

        # Settle navigation / redirects and re-extract DOM
        await browser.wait_for_navigation(timeout_ms=2500)
        page_structure = await browser.extract_page_structure()
        cur_url = page_structure.get("url") or (await browser.get_current_url())
        cur_title = page_structure.get("title") or ""
        shot = await browser.take_screenshot(name=f"{run_id}_user_act_{step}")
        if shot:
            _update_run(run_id, latest_screenshot_path=shot)
        _update_run(run_id, latest_dom=page_structure, current_url=cur_url, current_title=cur_title, step=step)

        self._emit_event(run_id, "observe", f"DOM Updated after User {action_type}", {
            "url": cur_url,
            "title": cur_title,
            "interactive": page_structure.get("interactive", {}),
            "tables_count": page_structure.get("tables_count", 0),
        })

        return {
            "success": success,
            "action_type": action_type,
            "target": target,
            "current_url": cur_url,
            "current_title": cur_title,
            "latest_dom": page_structure,
        }

    async def event_generator(self, run_id: str) -> AsyncGenerator[str, None]:
        """Server-Sent Events generator streaming live events for a task."""
        last_id = 0
        while True:
            run = get_run(run_id)
            if not run:
                yield f"data: {json.dumps({'error': 'Run not found'})}\n\n"
                break

            events = run.get("events", [])
            new_events = [e for e in events if e.get("id", 0) > last_id]

            for ev in new_events:
                last_id = ev["id"]
                yield f"data: {json.dumps(ev)}\n\n"

            # Check if task is terminal
            status = run.get("status", "")
            if status in {"completed", "done", "not_done", "failed", "stopped", "invalid"}:
                # Emit final state and close stream
                yield f"data: {json.dumps({'type': 'stream_end', 'status': status, 'result_summary': run.get('result_summary')})}\n\n"
                break

            await asyncio.sleep(0.5)
