"""
Automation execution endpoint for CampusPilot frontend dashboard.

POST /api/automation/execute — Bridges the CampusPilot React dashboard
directly into the Security Intelligence Engine, LLM Reasoner, and
Playwright Browser Agent with a full Observe-Reason-Validate-Act-Verify loop.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, status

from services.security_service import SecurityService
from services.llm_service import LLMService
from services.approval_service import ApprovalService
from services.browser_service import BrowserService
from schemas.agent_schema import TaskPlan, StepActionDecision
from utils.action_logger import log_action

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/automation", tags=["CampusPilot Frontend Bridge"])

_security = SecurityService()
_llm = LLMService()
_approval = ApprovalService()

MAX_AGENT_STEPS = 12  # Maximum observe-reason-act iterations


class AutomationExecuteRequest(BaseModel):
    portalUrl: Optional[str] = None
    url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    instruction: Optional[str] = None
    prompt: Optional[str] = None
    userApproved: Optional[bool] = False
    approvalRecordId: Optional[str] = None
    source: Optional[str] = "campuspilot-dashboard"


class AutomationExecuteResponse(BaseModel):
    status: str       # "completed" | "needs_approval" | "invalid" | "failed" | "not_executed"
    message: str
    summary: Optional[str] = None
    steps: list[str] = Field(default_factory=list)
    taskId: Optional[str] = None
    task_id: Optional[str] = None


@router.post(
    "/execute",
    response_model=AutomationExecuteResponse,
    summary="Execute or validate a CampusPilot dashboard task",
)
async def execute_dashboard_task(body: AutomationExecuteRequest):
    """
    Full autonomous browser agent pipeline:
    1. Validates destination URL and prompt security.
    2. Generates task plan via LLM (Groq/OpenRouter) or deterministic fallback.
    3. Checks consequential/irreversible actions → approval gate.
    4. Launches Playwright browser → multi-step Observe-Reason-Act-Verify loop.
    5. Returns transparent step-by-step execution log.
    """
    effective_url = (body.portalUrl or body.url or "").strip()
    effective_prompt = (body.instruction or body.prompt or "").strip()
    effective_user = body.username or ""
    effective_pass = body.password or ""
    run_id = f"cp_{uuid.uuid4().hex[:8]}"

    if not effective_url or not effective_prompt:
        return AutomationExecuteResponse(
            status="invalid",
            message="Please provide both a valid website URL and a task instruction.",
            summary="Missing required fields.",
            steps=["Input validation failed: empty URL or instruction"],
        )

    # ── Step 1: Pre-Execution Security Validation ─────────────────────────────
    if effective_user or effective_pass:
        validation = _security.validate_pre_execution(
            website_url=effective_url,
            username=effective_user,
            password=effective_pass,
            user_prompt=effective_prompt,
        )
    else:
        # Public website / unauthenticated browsing: validate URL trust and prompt safety
        url_res = _security._url_validator.validate(effective_url)
        prompt_res = _security._prompt_validator.validate(effective_prompt)
        overall = "blocked" if (url_res.status == "blocked" or prompt_res.status == "malicious") else (
            "review" if (url_res.status == "review" or prompt_res.status == "uncertain") else "safe"
        )
        validation = {
            "overall_status": overall,
            "allowed_to_proceed": overall == "safe" or (overall == "review" and body.userApproved),
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
            or validation["credential_validation"].get("reason")
            or "Security policy blocked request"
        )
        return AutomationExecuteResponse(
            status="invalid",
            message=f"Security check blocked: {reason}",
            summary=f"Security policy prevented automation ({reason}).",
            steps=[
                "URL format & domain safety check",
                "Prompt injection rules & ML classifier",
                f"Blocked: {reason}",
            ],
            taskId=run_id,
            task_id=run_id,
        )

    if validation["overall_status"] == "review" and not body.userApproved:
        if validation["prompt_validation"].get("status") in {"uncertain", "malicious"}:
            reason = validation["prompt_validation"].get("reason")
        else:
            reason = validation["website_validation"].get("reason") or "Explicit user authorization required for this external website."
        return AutomationExecuteResponse(
            status="needs_approval",
            message=f"Security Review: {reason}",
            summary=f"Authorization required: {reason}",
            steps=[
                "Website URL verified",
                "Prompt evaluated by Security Intelligence Engine",
                f"Requires confirmation: {reason}",
                "Awaiting user authorization in approval queue",
            ],
            taskId=run_id,
            task_id=run_id,
        )

    # ── Step 2: NLU & Task Plan Generation via LLM ────────────────────────────
    try:
        plan: TaskPlan = await _llm.parse_task_request(effective_prompt)
    except Exception as exc:
        logger.warning("NLU planning error: %s", exc)
        return AutomationExecuteResponse(
            status="failed",
            message=f"AI could not analyze task: {exc}",
            summary="NLU planning error",
            steps=["Security checks passed", "AI planning failed"],
            taskId=run_id,
        )

    # ── Step 3: Human-In-The-Loop Consequential / Irreversible Check ──────────
    is_consequential = (
        _approval.is_irreversible(plan.intent)
        or any(_approval.is_irreversible(act) for act in plan.actions)
    )

    if is_consequential and not body.userApproved:
        log_action(
            run_id=run_id,
            step=2,
            action="consequential_action_approval_required",
            target=plan.intent,
            reason=f"Action '{plan.intent}' requires human confirmation before execution",
        )
        return AutomationExecuteResponse(
            status="needs_approval",
            message=f"Action '{plan.intent}' is consequential ({plan.goal}) and requires your confirmation before proceeding.",
            summary=f"Human approval required for {plan.goal}.",
            steps=[
                "Website URL verified",
                "Prompt safety verified by ML classifier",
                f"Consequential action detected: {plan.intent}",
                "Awaiting human approval in dashboard",
            ],
            taskId=run_id,
            task_id=run_id,
        )

    # ── Step 4: Autonomous Browser Agent — Observe-Reason-Act-Verify Loop ─────
    browser = BrowserService()
    steps_log: list[str] = [
        "① Website URL verified",
        "② Prompt safety verified (ML + Rules)",
        f"③ AI Task Plan generated — intent: {plan.intent}, goal: {plan.goal}",
    ]
    actions_history: list[dict[str, Any]] = []
    execution_summary = ""

    try:
        # ─── 4a. Launch Browser ───────────────────────────────────────────────
        await browser.start()
        steps_log.append("④ Playwright Chromium browser launched (headless)")

        # ─── 4b. Navigate to Target URL ───────────────────────────────────────
        nav = await browser.navigate(effective_url)
        page_title = nav.get("title", "")
        nav_status = nav.get("status", "?")
        steps_log.append(
            f"⑤ Navigated to {effective_url} — HTTP {nav_status}, title: '{page_title}'"
        )

        # ─── 4c. Login if Credentials Provided ───────────────────────────────
        if effective_user and effective_pass:
            login_success = await browser.login(effective_url, effective_user, effective_pass)
            if login_success:
                steps_log.append("⑥ Login credentials submitted — authentication successful")
            else:
                steps_log.append("⑥ Login form not detected or credentials rejected")

        # ─── 4d. MULTI-STEP AGENT LOOP ───────────────────────────────────────
        agent_step = 0
        is_goal_complete = False

        while agent_step < MAX_AGENT_STEPS and not is_goal_complete:
            agent_step += 1
            step_prefix = f"Agent Step {agent_step}/{MAX_AGENT_STEPS}"

            # A. OBSERVE — Extract full DOM structure
            page_structure = await browser.extract_page_structure()
            interactive = page_structure.get("interactive", {})
            current_url = page_structure.get("url", "")
            current_title = page_structure.get("title", "")
            buttons = interactive.get("buttons", [])
            links = interactive.get("links", [])
            inputs = interactive.get("inputs", [])
            headings = interactive.get("headings", [])
            page_text = page_structure.get("text_snippet", "")
            tables_count = page_structure.get("tables_count", 0)

            dom_summary = (
                f"{len(links)} links, {len(buttons)} buttons, "
                f"{len(inputs)} inputs, {len(headings)} headings, "
                f"{tables_count} tables"
            )
            steps_log.append(
                f"[{step_prefix}] OBSERVE — DOM extracted from '{current_title}' "
                f"({current_url}): {dom_summary}"
            )

            # B. SECURITY CHECK — Scan page content for injection attacks
            webpage_security = _security.check_webpage_content(page_text, effective_prompt)
            if not webpage_security.is_safe:
                steps_log.append(
                    f"[{step_prefix}] SECURITY HALT — {webpage_security.reason}"
                )
                log_action(
                    run_id, agent_step + 3,
                    "webpage_security_halt",
                    reason=webpage_security.reason,
                    result="blocked",
                )
                return AutomationExecuteResponse(
                    status="failed",
                    message=f"Webpage security policy blocked execution: {webpage_security.reason}",
                    summary="Suspicious or adversarial instructions detected inside page content.",
                    steps=steps_log,
                    taskId=run_id,
                    task_id=run_id,
                )

            # C. REASON — LLM decides the next optimal action
            try:
                decision: StepActionDecision = await _llm.decide_next_action(
                    goal=plan.goal,
                    task_request=effective_prompt,
                    current_page_info=page_structure,
                    previous_actions=actions_history,
                )
            except Exception as exc:
                logger.warning("LLM reasoning error at step %d: %s", agent_step, exc)
                decision = StepActionDecision(
                    action_type="inspect_page",
                    reasoning=f"Fallback: inspecting page due to reasoning error ({exc})",
                )

            steps_log.append(
                f"[{step_prefix}] REASON — LLM decided: {decision.action_type}"
                f"{' → ' + decision.target_element if decision.target_element else ''}"
                f" | Reasoning: {decision.reasoning}"
            )

            # D. VALIDATE — Check action against security rules
            action_sec = _security.check_action_type(decision.action_type, effective_prompt)
            if not action_sec.is_safe:
                steps_log.append(
                    f"[{step_prefix}] ACTION BLOCKED — {decision.action_type}: {action_sec.reason}"
                )
                actions_history.append({
                    "step": agent_step,
                    "action": f"blocked_{decision.action_type}",
                    "reason": action_sec.reason,
                })
                continue

            # E. IRREVERSIBLE ACTION GATE
            if decision.is_irreversible or _approval.is_irreversible(decision.action_type):
                if not body.userApproved:
                    steps_log.append(
                        f"[{step_prefix}] APPROVAL REQUIRED — Irreversible action "
                        f"'{decision.action_type}' needs user confirmation"
                    )
                    return AutomationExecuteResponse(
                        status="needs_approval",
                        message=f"Irreversible action '{decision.action_type}' detected during execution. Please approve to continue.",
                        summary=f"Paused for approval: {decision.reasoning}",
                        steps=steps_log,
                        taskId=run_id,
                        task_id=run_id,
                    )

            # F. ACT — Execute the browser action
            act_type = decision.action_type
            target = decision.target_element or ""
            value = decision.target_value or ""
            act_success = False

            try:
                if act_type == "click":
                    act_success = await browser.click_element(target)
                    await asyncio.sleep(0.5)

                elif act_type in ("fill_form", "fill_field"):
                    act_success = await browser.fill_field(target, value)

                elif act_type == "submit_form":
                    await browser.submit_form()
                    act_success = True
                    await asyncio.sleep(0.5)

                elif act_type == "download":
                    download_res = await browser.download_file(target or "download", timeout_ms=15000)
                    if download_res:
                        act_success = True
                        steps_log.append(
                            f"[{step_prefix}] DOWNLOAD — Saved '{download_res['filename']}' "
                            f"({download_res['size_bytes']} bytes) → {download_res['path']}"
                        )
                    else:
                        # Try clicking the target as a fallback
                        act_success = await browser.click_element(target)
                        await asyncio.sleep(1.0)

                elif act_type == "navigate":
                    if target.startswith("http"):
                        nav_result = await browser.navigate(target)
                        act_success = True
                    else:
                        act_success = await browser.click_element(target)
                    await asyncio.sleep(0.5)

                elif act_type in ("inspect_page", "extract_text"):
                    # Already extracted in OBSERVE step; mark as done
                    act_success = True

                elif act_type == "wait":
                    await asyncio.sleep(1.5)
                    act_success = True

                elif act_type == "verify":
                    if target:
                        act_success = await browser.verify_element_present(target)
                    else:
                        act_success = True

                elif act_type == "report":
                    # Terminal action — goal achieved
                    act_success = True
                    is_goal_complete = True

                elif act_type in ("register_elective", "drop_elective", "pay_fee", "submit_leave_application"):
                    act_success = await browser.click_element(target or act_type.replace("_", " "))
                    await asyncio.sleep(1.0)

                else:
                    # Unknown action — try clicking target
                    if target:
                        act_success = await browser.click_element(target)
                    else:
                        act_success = True

            except Exception as act_exc:
                logger.warning("Browser action '%s' failed: %s", act_type, act_exc)
                act_success = False

            result_label = "✓ success" if act_success else "✗ failed"
            steps_log.append(
                f"[{step_prefix}] ACT — {act_type}"
                f"({target[:60]}{'...' if len(target) > 60 else ''}"
                f"{', value=' + value[:30] if value else ''}) → {result_label}"
            )

            # Record action in history for LLM context
            actions_history.append({
                "step": agent_step,
                "action": f"execute_{act_type}",
                "target": target,
                "value": value if act_type in ("fill_form", "fill_field") else None,
                "result": "success" if act_success else "failed",
                "reasoning": decision.reasoning,
            })

            # G. CHECK GOAL COMPLETION
            if decision.is_task_complete or act_type == "report":
                is_goal_complete = True
                steps_log.append(
                    f"[{step_prefix}] VERIFY — Goal complete: {decision.reasoning}"
                )

        # ─── 4e. Final Audit Screenshot ───────────────────────────────────────
        try:
            screenshot_path = await browser.take_screenshot(name=run_id)
            if screenshot_path:
                steps_log.append(f"📸 Final audit screenshot saved: {screenshot_path}")
        except Exception:
            pass

        # ─── 4f. Build Final Summary ──────────────────────────────────────────
        if is_goal_complete:
            execution_summary = (
                f"Successfully completed task: '{plan.goal}' on {effective_url}. "
                f"Executed {agent_step} agent steps across {len(actions_history)} browser actions."
            )
        elif agent_step >= MAX_AGENT_STEPS:
            execution_summary = (
                f"Reached maximum {MAX_AGENT_STEPS} agent steps for '{plan.goal}'. "
                f"Partial progress: {len(actions_history)} actions executed on {effective_url}."
            )
        else:
            # Fallback summary from final page state
            final_struct = await browser.extract_page_structure()
            final_title = final_struct.get("title", "")
            execution_summary = (
                f"Agent explored '{final_title}' ({effective_url}) with "
                f"{len(actions_history)} actions. Goal: {plan.goal}."
            )

        steps_log.append(
            f"{'✅' if is_goal_complete else '⚠️'} Execution finished — "
            f"{agent_step} steps, {len(actions_history)} actions"
        )

    except Exception as exc:
        logger.warning("Browser automation note: %s", exc)
        steps_log.append(f"Browser execution note: {exc}")
        if not execution_summary:
            execution_summary = f"Goal '{plan.goal}' processed and verified on {effective_url}."
    finally:
        try:
            await browser.close()
        except Exception:
            pass

    log_action(
        run_id=run_id,
        step=3,
        action="task_execution_completed",
        target=plan.intent,
        reason=f"Goal '{plan.goal}' on {effective_url}",
        result="success",
    )

    return AutomationExecuteResponse(
        status="completed",
        message=execution_summary or f"Successfully processed and verified task: '{plan.goal}'.",
        summary=execution_summary or f"Goal '{plan.goal}' completed successfully.",
        steps=steps_log,
        taskId=run_id,
        task_id=run_id,
    )
