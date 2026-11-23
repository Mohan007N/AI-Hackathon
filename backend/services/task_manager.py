"""
Task manager — orchestrates the full agent workflow for one run.

Coordinates: PortalService → LLMService → BrowserService
           → SecurityService → ApprovalService → ReportService
           → ActionLogger

State is held in-memory per run_id. For production use a Redis/DB store.
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
    def __init__(self):
        self._portal = PortalService()
        self._llm = LLMService()
        self._security = SecurityService()
        self._approval = ApprovalService()
        self._report = ReportService(self._portal)

    # ── Phase 1: Start run ────────────────────────────────────────────────────

    async def start_task_run(
        self,
        task_id: str,
        website_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        user_prompt: Optional[str] = None,
        security_validation: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Call organizer portal to create a new run, then fetch task request."""
        run_data = await self._portal.start_run(task_id)
        run_id: str = run_data.get("run_id") or run_data.get("id", "")
        if not run_id:
            raise PortalServiceError("Portal did not return a run_id.")

        request_data = await self._portal.get_run_request(run_id)
        effective_portal_url: str = website_url or request_data.get("portal_url", "")
        effective_task_request: str = user_prompt or request_data.get("task_request", "")
        effective_username: str = username or request_data.get("username", "")
        effective_password: str = password or request_data.get("password", "")

        _set_run(run_id, {
            "run_id": run_id,
            "task_id": task_id,
            "portal_url": effective_portal_url,
            # credentials stored in-memory only, never logged or sent to LLM/ML
            "_username": effective_username,
            "_password": effective_password,
            "task_request": effective_task_request,
            "status": "started",
            "step": 0,
            "current_action": None,
            "error": None,
            "result_status": None,
            "result_summary": None,
            "security_validation": security_validation,
            "started_at": datetime.now(tz=timezone.utc).isoformat(),
        })

        log_action(run_id, 0, "run_started",
                   target=task_id,
                   reason="Organizer run created successfully after security checks")

        return {
            "run_id": run_id,
            "task_id": task_id,
            "portal_url": effective_portal_url,
            "request_text": effective_task_request,
            "status": "started",
            "security_validation": security_validation,
        }

    # ── Phase 2: Execute agent workflow ───────────────────────────────────────

    async def execute(self, run_id: str) -> dict[str, Any]:
        """Full async agent workflow. Run in background task."""
        run = get_run(run_id)
        if not run:
            raise ValueError(f"run_id '{run_id}' not found.")
        if run["status"] in {"running", "done", "not_done"}:
            return {"status": run["status"], "message": "Already executed or running."}

        _update_run(run_id, status="running")
        browser = BrowserService()

        try:
            await self._run_workflow(run_id, run, browser)
        except Exception as exc:
            logger.error("Workflow error run_id=%s: %s", run_id, exc, exc_info=True)
            _update_run(run_id, status="error", error=str(exc))
            log_action(run_id, run["step"], "workflow_error", reason=str(exc))
        finally:
            await browser.close()

        return get_run(run_id)

    async def _run_workflow(
        self, run_id: str, run: dict, browser: BrowserService
    ) -> None:
        step = 0

        def next_step(action, **kwargs):
            nonlocal step
            step += 1
            _update_run(run_id, step=step, current_action=action)
            log_action(run_id, step, action, **kwargs)

        task_request: str = run["task_request"]
        portal_url: str = run["portal_url"]
        task_id: str = run["task_id"]

        # ── 1. LLM: parse task request ──────────────────────────────────────
        next_step("llm_parse_request", reason="Understanding student's natural-language request")
        try:
            plan = await self._llm.parse_task_request(task_request)
        except LLMServiceError as exc:
            next_step("llm_failed", reason=str(exc), result="error")
            _update_run(run_id, status="not_done", result_status="not_done",
                        result_summary=f"LLM failed: {exc}")
            return

        next_step("llm_plan_received",
                  value=plan.get("goal_summary"),
                  reason="LLM produced validated action plan")

        # ── 2. Browser: start and login ─────────────────────────────────────
        next_step("browser_start", reason="Launching browser")
        await browser.start()

        next_step("browser_login",
                  url=portal_url,
                  reason="Logging into student portal with provided credentials")
        login_ok = await browser.login(
            portal_url,
            run["_username"],
            run["_password"],
        )
        if not login_ok:
            next_step("login_failed", reason="Could not locate login form", result="error")
            _update_run(run_id, status="not_done",
                        result_status="not_done",
                        result_summary="Login failed — could not locate form fields.")
            return

        # ── 3. Inspect page content and check for injection ─────────────────
        next_step("inspect_page", url=await browser.get_current_url(),
                  reason="Extracting page text for security check")
        page_text = await browser.get_page_text()
        security_result = self._security.check_webpage_content(page_text, task_request)

        if not security_result.is_safe:
            next_step("security_block",
                      reason=security_result.reason,
                      result=f"classification={security_result.classification}")
            _update_run(run_id, status="not_done",
                        result_status="not_done",
                        result_summary=f"Security block: {security_result.reason}")
            return

        # ── 4. Execute planned actions ──────────────────────────────────────
        actions: list[str] = plan.get("actions", [])
        for action_type in actions:
            # Validate each action before execution
            sec = self._security.check_action_type(action_type, task_request)
            if not sec.is_safe:
                next_step("action_blocked",
                          target=action_type,
                          reason=sec.reason,
                          result="blocked")
                continue

            # Irreversible action — require official confirmation
            if self._approval.is_irreversible(action_type):
                action_desc = f"Execute '{action_type}' for task: {plan.get('goal_summary', task_request)}"
                next_step("confirmation_requested",
                          target=action_type,
                          reason="Irreversible action requires official confirmation")

                _update_run(run_id, status="awaiting_approval", current_action=action_type)
                confirm_result = await self._approval.request_confirmation(
                    action_desc, portal_url, run_id
                )
                next_step("confirmation_result",
                          target=action_type,
                          value=confirm_result.get("approved"),
                          reason=confirm_result.get("message"))

                if not confirm_result["approved"]:
                    _update_run(run_id, status="not_done",
                                result_status="not_done",
                                result_summary="Irreversible action denied by official confirmation.")
                    return

                _update_run(run_id, status="running")

            # Execute reversible action
            next_step(f"execute_{action_type}", target=action_type,
                      reason=f"Executing plan step: {action_type}")
            await self._dispatch_action(browser, action_type, plan)

        # ── 5. Verify result ────────────────────────────────────────────────
        next_step("verify_result", reason="Checking portal state after all actions")
        final_text = await browser.get_page_text()
        verified = self._verify_success(plan.get("intent", ""), final_text)
        result_status = "done" if verified else "not_done"
        summary = (
            f"Task '{plan.get('goal_summary', task_request)}' completed and verified."
            if verified else
            f"Task '{plan.get('goal_summary', task_request)}' could not be verified on portal."
        )

        next_step("workflow_complete", result=result_status, reason=summary)
        _update_run(run_id, status=result_status,
                    result_status=result_status,
                    result_summary=summary)

        # ── 6. Submit report ────────────────────────────────────────────────
        await self._report.submit(run_id, task_id, result_status, summary)
        next_step("report_submitted", reason="Final report submitted to organizer portal")

    async def _dispatch_action(
        self, browser: BrowserService, action_type: str, plan: dict
    ) -> None:
        """Route an action type to the appropriate browser call."""
        if action_type == "navigate":
            pass   # already navigated during login
        elif action_type == "inspect_page":
            await browser.get_page_text()
        elif action_type == "extract_text":
            await browser.get_page_text()
        elif action_type == "verify":
            await browser.get_page_text()
        elif action_type == "wait":
            await asyncio.sleep(2)
        # More specific actions (click, fill_form, etc.) require
        # portal-specific instructions fed back from the page inspection.
        # They are dispatched by the agent loop once the page state is known.

    def _verify_success(self, intent: str, page_text: str) -> bool:
        """
        Heuristic: check if the page text contains success indicators
        relevant to the declared intent. Extend per task type.
        """
        success_patterns: dict[str, list[str]] = {
            "register_elective": ["successfully registered", "registration confirmed", "elective added"],
            "download_hall_ticket": ["hall ticket", "admit card", "download"],
            "check_fees": ["fee", "amount", "paid", "outstanding"],
            "submit_leave": ["leave application submitted", "application received"],
            "check_electives": ["elective", "available", "course"],
        }
        lower = page_text.lower()
        for kw in success_patterns.get(intent, []):
            if kw in lower:
                return True
        return False

    # ── Status & logs ─────────────────────────────────────────────────────────

    def get_status(self, run_id: str) -> Optional[dict[str, Any]]:
        return get_run(run_id)

    def get_logs(self, run_id: str) -> list[dict[str, Any]]:
        return read_logs(run_id)

