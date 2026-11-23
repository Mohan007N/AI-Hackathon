"""
Task manager — orchestrates the complete AI agent workflow for one run.

Loop architecture:
  Observe → Reason (LLM) → Validate (Security) → Act (Browser) → Verify

Coordinates:
  PortalService → LLMService → BrowserService → SecurityService
  → ApprovalService → ReportService → ActionLogger

Credentials contract:
  Credentials are kept in-memory only during task execution and
  are NEVER logged, persisted, or shared with LLMs/external APIs.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from services.portal_service import PortalService, PortalServiceError
from services.llm_service import LLMService, LLMServiceError
from services.browser_service import BrowserService, BrowserServiceError
from services.security_service import SecurityService
from services.approval_service import ApprovalService
from services.report_service import ReportService
from utils.action_logger import log_action, read_logs
from schemas.agent_schema import TaskPlan, StepActionDecision

logger = logging.getLogger(__name__)

# ── In-memory run store ────────────────────────────────────────────────────────
_runs: dict[str, dict[str, Any]] = {}


def get_run(run_id: str) -> Optional[dict[str, Any]]:
    return _runs.get(run_id)


def _set_run(run_id: str, data: dict[str, Any]) -> None:
    _runs[run_id] = data


def _update_run(run_id: str, **kwargs) -> None:
    if run_id in _runs:
        _runs[run_id].update(kwargs)


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

    # ── Phase 1: Start Run ────────────────────────────────────────────────────

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
            # In-memory only: NEVER logged or serialized to responses
            "_username": effective_username,
            "_password": effective_password,
            "task_request": effective_task_request,
            "status": "started",
            "step": 0,
            "current_action": None,
            "plan": None,
            "actions_history": [],
            "error": None,
            "result_status": None,
            "result_summary": None,
            "security_validation": security_validation,
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
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

    # ── Phase 2: Execute Agent Workflow ───────────────────────────────────────

    async def execute(self, run_id: str, browser: Optional[BrowserService] = None) -> dict[str, Any]:
        """Start the asynchronous agent workflow for the given run_id."""
        run = get_run(run_id)
        if not run:
            raise ValueError(f"run_id '{run_id}' not found.")
        if run["status"] in {"running", "done", "not_done"}:
            return {"status": run["status"], "message": f"Run already in status '{run['status']}'."}

        _update_run(run_id, status="running")
        owns_browser = browser is None
        active_browser = browser or BrowserService()

        try:
            await self._run_workflow(run_id, run, active_browser)
        except Exception as exc:
            logger.error("Workflow failed unexpectedly for run_id=%s: %s", run_id, exc, exc_info=True)
            _update_run(run_id, status="error", error=str(exc), result_status="not_done", result_summary=f"Error: {exc}")
            log_action(run_id, run.get("step", 0), "workflow_error", reason=str(exc))
            # Attempt error report submission
            try:
                await self._report.submit(run_id, run["task_id"], "not_done", f"Internal error: {exc}")
            except Exception:
                pass
        finally:
            if owns_browser:
                await active_browser.close()

        return get_run(run_id) or {}

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
        portal_url: str = run["portal_url"]
        task_id: str = run["task_id"]
        username: str = run["_username"]
        password: str = run["_password"]

        # ── 1. NLU: Task Understanding & Plan Generation ──────────────────────
        next_step("llm_parse_request", reason="Analyzing student request into structured task plan")
        try:
            plan: TaskPlan = await self._llm.parse_task_request(task_request)
        except LLMServiceError as exc:
            next_step("llm_failed", reason=str(exc), result="error")
            _update_run(run_id, status="not_done", result_status="not_done", result_summary=f"NLU planning failed: {exc}")
            await self._report.submit(run_id, task_id, "not_done", f"NLU planning failed: {exc}")
            return

        _update_run(run_id, plan=plan.model_dump())
        next_step(
            "llm_plan_received",
            value=plan.intent,
            reason=f"Goal: {plan.goal} | Constraints: {', '.join(plan.constraints) or 'none'}",
        )

        # ── 2. Browser: Launch & Authenticate ─────────────────────────────────
        next_step("browser_start", reason="Launching Chromium automation session")
        await browser.start()

        next_step("browser_login", url=portal_url, reason="Navigating to portal and injecting credentials into DOM")
        login_ok = await browser.login(portal_url, username, password)
        if not login_ok:
            next_step("login_failed", reason="Could not locate or submit login form fields", result="error")
            _update_run(
                run_id,
                status="not_done",
                result_status="not_done",
                result_summary="Login failed: could not locate login form or portal rejected credentials.",
            )
            await self._report.submit(run_id, task_id, "not_done", "Login failed: credentials rejected or form missing.")
            return

        next_step("login_success", reason="Authenticated into student portal dashboard")

        # ── 3. Autonomous Observe-Reason-Validate-Act-Verify Loop ─────────────
        MAX_STEPS = 12
        executed_actions_count = 0
        is_completed = False

        while executed_actions_count < MAX_STEPS and not is_completed:
            executed_actions_count += 1

            # A. OBSERVE: Extract current DOM text & interactive structure
            if await browser.is_session_expired():
                next_step("session_expired", reason="Session expired; re-authenticating")
                relogin_ok = await browser.login(portal_url, username, password)
                if not relogin_ok:
                    next_step("relogin_failed", reason="Session recovery failed", result="error")
                    _update_run(run_id, status="not_done", result_status="not_done", result_summary="Session expired and re-login failed.")
                    await self._report.submit(run_id, task_id, "not_done", "Session expired during execution.")
                    return

            page_structure = await browser.extract_page_structure()
            page_text = await browser.get_page_text()

            # B. WEBPAGE SECURITY: Inspect page content for indirect prompt injection
            security_check = self._security.check_webpage_content(page_text, task_request)
            if not security_check.is_safe:
                next_step(
                    "security_block",
                    reason=security_check.reason,
                    result=f"classification={security_check.classification}",
                )
                summary = f"Security violation detected in portal webpage content: {security_check.reason}"
                _update_run(run_id, status="not_done", result_status="not_done", result_summary=summary)
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

            # D. VALIDATE ACTION SECURITY: Enforce allowable action types
            action_sec = self._security.check_action_type(decision.action_type, task_request)
            if not action_sec.is_safe:
                next_step(
                    "action_blocked",
                    target=decision.action_type,
                    reason=action_sec.reason,
                    result="blocked",
                )
                continue

            # E. IRREVERSIBLE ACTIONS GATE: Official confirm.py confirmation
            if decision.is_irreversible or self._approval.is_irreversible(decision.action_type):
                action_desc = f"Execute '{decision.action_type}' for task: {plan.goal}"
                next_step(
                    "confirmation_requested",
                    target=decision.action_type,
                    reason=f"Irreversible action '{decision.action_type}' requires official confirmation",
                )

                _update_run(run_id, status="awaiting_approval", current_action=decision.action_type)
                confirm_result = await self._approval.request_confirmation(
                    action_description=action_desc,
                    base_url=portal_url,
                    run_id=run_id,
                )

                approved = confirm_result.get("approved", False)
                next_step(
                    "confirmation_result",
                    target=decision.action_type,
                    value=approved,
                    reason=confirm_result.get("message", ""),
                )

                if not approved:
                    summary = f"Irreversible action '{decision.action_type}' was denied or confirm.py is unavailable."
                    _update_run(run_id, status="not_done", result_status="not_done", result_summary=summary)
                    await self._report.submit(run_id, task_id, "not_done", summary)
                    return

                # Restored to running state upon confirmation
                _update_run(run_id, status="running")

            # F. ACT: Dispatch action to browser service
            act_type = decision.action_type
            target = decision.target_element or ""
            val = decision.target_value or ""

            next_step(f"execute_{act_type}", target=target, reason=decision.reasoning)
            act_success = await self._dispatch_browser_action(browser, act_type, target, val)

            # G. VERIFY: State verification
            next_step(
                f"verify_action_{act_type}",
                result="success" if act_success else "partial/failed",
                reason=f"Action '{act_type}' dispatched",
            )

            if decision.is_task_complete or act_type in {"report"}:
                is_completed = True
                break

        # ── 4. Final Verification & Outcome Reporting ─────────────────────────
        next_step("final_verification", reason="Verifying portal state and outcomes against student goal")
        final_text = await browser.get_page_text()
        verified = self._verify_success(plan.intent, final_text)

        try:
            shot_res = browser.take_screenshot(name=f"{run_id}_final")
            if asyncio.iscoroutine(shot_res):
                await shot_res
        except Exception as exc:
            logger.debug("Screenshot skipped or failed: %s", exc)

        result_status = "done" if verified else "not_done"
        summary = (
            f"Task '{plan.goal}' completed and verified on student portal."
            if verified else
            f"Task '{plan.goal}' execution ended. Outcome could not be verified on portal."
        )

        next_step("workflow_complete", result=result_status, reason=summary)
        _update_run(
            run_id,
            status=result_status,
            result_status=result_status,
            result_summary=summary,
        )

        # Submit final report to organizer portal
        await self._report.submit(run_id, task_id, result_status, summary)
        next_step("report_submitted", reason="Final action log and execution report submitted to organizer portal")

    async def _dispatch_browser_action(
        self, browser: BrowserService, action_type: str, target: str, value: str
    ) -> bool:
        """Execute a validated action on the live browser."""
        try:
            if action_type == "click":
                return await browser.click_element(target)
            elif action_type == "fill_form":
                return await browser.fill_field(target, value)
            elif action_type == "submit_form":
                await browser.submit_form()
                return True
            elif action_type == "download":
                res = await browser.download_file(target or "download")
                return res is not None
            elif action_type == "navigate":
                if target.startswith("http"):
                    await browser.navigate(target)
                else:
                    await browser.click_element(target)
                return True
            elif action_type in {"inspect_page", "extract_text"}:
                await browser.get_page_text()
                return True
            elif action_type == "wait":
                await asyncio.sleep(1.0)
                return True
            elif action_type in {"register_elective", "drop_elective", "pay_fee", "submit_leave_application"}:
                # Irreversible operations typically involve confirming a final button
                return await browser.click_element(target or action_type.replace("_", " "))
            return True
        except Exception as exc:
            logger.warning("Browser dispatch error for action '%s': %s", action_type, exc)
            return False

    def _verify_success(self, intent: str, page_text: str) -> bool:
        """Semantic verification heuristics on post-execution portal text."""
        lower = page_text.lower()
        success_indicators: dict[str, list[str]] = {
            "register_elective": ["registered", "enrolled", "elective added", "success", "confirmed", "my courses"],
            "check_electives": ["elective", "course", "available", "department", "credits"],
            "download_hall_ticket": ["hall ticket", "admit card", "examination", "download", "pdf"],
            "check_attendance": ["attendance", "percentage", "present", "absent", "subject", "classes"],
            "check_fees": ["fee", "tuition", "dues", "amount", "paid", "receipt"],
            "pay_fee": ["payment successful", "transaction successful", "receipt generated", "paid"],
            "submit_leave": ["leave application submitted", "application received", "pending approval"],
            "check_timetable": ["timetable", "schedule", "monday", "tuesday", "period"],
            "general_query": ["portal", "student", "academic"],
        }

        keywords = success_indicators.get(intent, ["success", "student", "portal"])
        return any(kw in lower for kw in keywords)

    # ── Status & Logs Queries ─────────────────────────────────────────────────

    def get_status(self, run_id: str) -> Optional[dict[str, Any]]:
        return get_run(run_id)

    def get_logs(self, run_id: str) -> list[dict[str, Any]]:
        return read_logs(run_id)
