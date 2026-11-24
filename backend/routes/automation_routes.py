"""
Unified Automation execution API for CampusPilot dashboard.

Endpoints:
  POST /api/automation/start          — Create session and start background agent workflow.
  GET  /api/automation/{task_id}/status     — Get live task status, DOM structure, and step info.
  GET  /api/automation/{task_id}/events     — Live Server-Sent Events (SSE) stream of agent steps.
  GET  /api/automation/{task_id}/screenshot — Real Playwright screenshot (image/png).
  POST /api/automation/{task_id}/approval   — Human approval decision (resumes same session).
  POST /api/automation/{task_id}/stop       — Stop running task and close browser.
  POST /api/automation/execute              — Synchronous execution bridge (backward-compatible).
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from typing import Any, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, BackgroundTasks, HTTPException, Response, status
from fastapi.responses import FileResponse, StreamingResponse

from services.task_manager import TaskManager, get_run
from utils.action_logger import log_action

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/automation", tags=["CampusPilot Automation Bridge"])

_manager = TaskManager()

# 1x1 transparent PNG placeholder when screenshot is not yet captured
_BLANK_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
)


class AutomationStartRequest(BaseModel):
    url: Optional[str] = None
    portalUrl: Optional[str] = None
    instruction: Optional[str] = None
    prompt: Optional[str] = None
    username: Optional[str] = ""
    password: Optional[str] = ""
    userApproved: Optional[bool] = False
    approvalRecordId: Optional[str] = None
    source: Optional[str] = "campuspilot-dashboard"


class AutomationStartResponse(BaseModel):
    task_id: str
    status: str       # "started" | "needs_approval" | "invalid"
    message: str
    allowed: bool = True
    reason: Optional[str] = None


class AutomationApprovalRequest(BaseModel):
    approved: bool
    reason: Optional[str] = ""


class UserActionRequest(BaseModel):
    action_type: str = "click"  # "click" | "fill_form" | "navigate"
    target: str                 # element_id (elem_1) or semantic label / text / URL
    value: Optional[str] = ""


class AutomationStatusResponse(BaseModel):
    task_id: str
    status: str       # "started" | "running" | "needs_approval" | "completed" | "not_done" | "failed" | "stopped"
    step: int = 0
    current_action: Optional[str] = None
    current_url: Optional[str] = None
    current_title: Optional[str] = None
    result_status: Optional[str] = None
    result_summary: Optional[str] = None
    error: Optional[str] = None
    latest_dom: dict[str, Any] = Field(default_factory=dict)
    pending_approval: Optional[dict[str, Any]] = None
    downloaded_files: list[Any] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    has_screenshot: bool = False
    started_at: Optional[str] = None
    updated_at: Optional[str] = None
    prompt: Optional[str] = None
    goal: Optional[str] = None
    verified: bool = False
    verification_details: Optional[str] = None
    steps: list[str] = Field(default_factory=list)
    last_decision: Optional[dict[str, Any]] = None


class AutomationExecuteResponse(BaseModel):
    status: str       # "completed" | "needs_approval" | "invalid" | "failed" | "not_done" | "stopped"
    message: str
    summary: Optional[str] = None
    steps: list[str] = Field(default_factory=list)
    taskId: Optional[str] = None
    task_id: Optional[str] = None


# ── 1. Start Automation Task (Asynchronous) ───────────────────────────────────

@router.post(
    "/start",
    response_model=AutomationStartResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start an autonomous agent task session",
)
async def start_automation_task(body: AutomationStartRequest, background_tasks: BackgroundTasks):
    """
    Validates inputs, initializes TaskManager session, and kicks off
    the autonomous Observe-Reason-Act-Verify browser agent in the background.
    Returns session task_id immediately so frontend can enter live Agent Workspace.
    """
    effective_url = (body.portalUrl or body.url or "").strip()
    effective_prompt = (body.instruction or body.prompt or "").strip()

    if not effective_url or not effective_prompt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Both website URL and natural-language instruction are required.",
        )

    session = await _manager.create_automation_session(
        website_url=effective_url,
        instruction=effective_prompt,
        username=body.username or "",
        password=body.password or "",
        user_approved=bool(body.userApproved),
    )

    if not session.get("allowed", False):
        return AutomationStartResponse(
            task_id=session["task_id"],
            status=session["status"],
            message=session["message"],
            allowed=False,
            reason=session.get("reason"),
        )

    # Launch autonomous agent loop asynchronously in background
    task_id = session["task_id"]
    background_tasks.add_task(_manager.execute, task_id)

    return AutomationStartResponse(
        task_id=task_id,
        status="started",
        message="Agent session started. Connect to events stream or poll status.",
        allowed=True,
    )


# ── 2. Get Live Status ────────────────────────────────────────────────────────

@router.get(
    "/{task_id}/status",
    response_model=AutomationStatusResponse,
    summary="Get current status, DOM structure, and step log for a task",
)
async def get_task_status(task_id: str):
    run = _manager.get_status(task_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' not found.",
        )

    has_shot = bool(run.get("latest_screenshot_path") and os.path.exists(run["latest_screenshot_path"])) or (run.get("browser") is not None)

    return AutomationStatusResponse(
        task_id=task_id,
        status=run.get("status", "unknown"),
        step=run.get("step", 0),
        current_action=run.get("current_action"),
        current_url=run.get("current_url") or run.get("website_url"),
        current_title=run.get("current_title"),
        result_status=run.get("result_status"),
        result_summary=run.get("result_summary"),
        error=run.get("error"),
        latest_dom=run.get("latest_dom", {}),
        pending_approval=run.get("pending_approval"),
        downloaded_files=run.get("downloaded_files", []),
        events=run.get("events", [])[-50:],  # return latest 50 events
        has_screenshot=has_shot,
        started_at=run.get("started_at"),
        updated_at=run.get("updated_at"),
        prompt=run.get("task_request"),
        goal=run.get("plan", {}).get("goal") if isinstance(run.get("plan"), dict) else None,
        verified=bool(run.get("result_status") == "done"),
        verification_details=run.get("result_summary"),
        steps=[e.get("title", "") for e in run.get("events", []) if isinstance(e, dict) and e.get("title")],
        last_decision=run.get("last_decision"),
    )


# ── 3. Server-Sent Events (SSE) Stream ────────────────────────────────────────

@router.get(
    "/{task_id}/events",
    summary="Live SSE event stream of agent actions, DOM observations, and results",
)
async def get_task_events_stream(task_id: str):
    run = _manager.get_status(task_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' not found.",
        )

    return StreamingResponse(
        _manager.event_generator(task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── 4. Live Screenshot Retrieval ──────────────────────────────────────────────

@router.get(
    "/{task_id}/screenshot",
    summary="Get the latest real Playwright browser screenshot",
)
async def get_task_screenshot(task_id: str):
    run = _manager.get_status(task_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' not found.",
        )

    data = await _manager.get_screenshot_bytes(task_id)
    if not data:
        return Response(content=_BLANK_PNG, media_type="image/png")

    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


# ── 5. Human Approval Decision (Issue 6) ──────────────────────────────────────

@router.post(
    "/{task_id}/approval",
    summary="Approve or deny a paused consequential action in the same browser session",
)
@router.post(
    "/{task_id}/approve",
    summary="Approve or deny alias for live agent workspace",
)
async def respond_to_approval(task_id: str, body: AutomationApprovalRequest):
    run = _manager.get_status(task_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' not found.",
        )

    res = _manager.request_approval_decision(task_id, body.approved, body.reason or "")
    if not res.get("success", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=res.get("message", "Could not process approval."),
        )

    return res


# ── 6. Live DOM Inspection & User Manual Access ──────────────────────────────

@router.get(
    "/{task_id}/dom",
    summary="Get full structured DOM elements, tables, and interactive controls for user inspection",
)
async def get_task_dom(task_id: str):
    run = _manager.get_status(task_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' not found.",
        )
    return run.get("latest_dom", {})


@router.get(
    "/{task_id}/html",
    summary="Get live HTML content of the page for user preview",
)
async def get_task_html(task_id: str):
    run = _manager.get_status(task_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{task_id}' not found.")
    live_run = get_run(task_id)
    browser = live_run.get("browser") if live_run else None
    if browser and getattr(browser, "_page", None):
        try:
            content = await browser._page.content()
            return Response(content=content, media_type="text/html")
        except Exception:
            pass
    text = run.get("latest_dom", {}).get("text_snippet", "")
    return Response(content=f"<!DOCTYPE html><html><body><pre>{text}</pre></body></html>", media_type="text/html")


@router.post(
    "/{task_id}/act",
    summary="User manual action intervention on the live page DOM",
)
async def user_act_on_dom(task_id: str, body: UserActionRequest):
    run = _manager.get_status(task_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{task_id}' not found.")
    res = await _manager.manual_user_action(task_id, body.action_type, body.target, body.value or "")
    return res


# ── 7. Stop Task ──────────────────────────────────────────────────────────────

@router.post(
    "/{task_id}/stop",
    summary="Stop a running agent task and clean up browser session",
)
async def stop_task(task_id: str):
    run = _manager.get_status(task_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' not found.",
        )

    res = _manager.stop_task(task_id)
    return res


# ── 7. Download Task Artifact (Downloads) ──────────────────────────────────────

@router.get(
    "/{task_id}/download/{filename}",
    summary="Download a file retrieved by the agent during the task",
)
async def download_task_artifact(task_id: str, filename: str):
    # Verify task ownership and isolate downloads per task (Requirement 33)
    run = _manager.get_status(task_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' not found.",
        )

    safe_name = os.path.basename(filename)
    if not safe_name or safe_name != filename or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid filename or path traversal detected.",
        )

    # Verify that the file was actually downloaded and registered by THIS task
    downloaded = run.get("downloaded_files", [])
    task_file_entry = next(
        (f for f in downloaded if f.get("filename") == safe_name or (f.get("path") and os.path.basename(f["path"]) == safe_name)),
        None
    )
    if not task_file_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artifact '{safe_name}' does not belong to task '{task_id}'.",
        )

    # Resolve allowed file paths strictly
    verified_path = task_file_entry.get("path")
    if verified_path and os.path.exists(verified_path) and os.path.isfile(verified_path):
        return FileResponse(verified_path, filename=safe_name, media_type="application/octet-stream")

    # Fallback to standard download folders if path was relative
    for folder in ["downloads", "downloaded_files"]:
        candidate = os.path.join(os.getcwd(), folder, safe_name)
        if os.path.exists(candidate) and os.path.isfile(candidate):
            return FileResponse(candidate, filename=safe_name, media_type="application/octet-stream")

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Artifact '{safe_name}' not found on disk.",
    )


# ── 8. Synchronous Execution Bridge (Backward Compatibility & Tests) ─────────

@router.post(
    "/execute",
    response_model=AutomationExecuteResponse,
    summary="Synchronous execution endpoint for backward compatibility",
)
async def execute_dashboard_task(body: AutomationStartRequest):
    """
    Synchronous bridge for existing tests and CLI callers.
    Calls TaskManager, waits for completion, and returns structured summary.
    """
    effective_url = (body.portalUrl or body.url or "").strip()
    effective_prompt = (body.instruction or body.prompt or "").strip()

    if not effective_url or not effective_prompt:
        return AutomationExecuteResponse(
            status="invalid",
            message="Please provide both a valid website URL and a task instruction.",
            summary="Missing required fields.",
            steps=["Input validation failed: empty URL or instruction"],
        )

    session = await _manager.create_automation_session(
        website_url=effective_url,
        instruction=effective_prompt,
        username=body.username or "",
        password=body.password or "",
        user_approved=bool(body.userApproved),
    )

    task_id = session["task_id"]

    if not session.get("allowed", False) or session.get("status") == "needs_approval":
        steps = [
            "URL format & domain safety check",
            "Prompt injection rules & ML classifier",
            f"Result: {session.get('reason') or session.get('status')}",
        ]
        return AutomationExecuteResponse(
            status=session["status"],
            message=session["message"],
            summary=session.get("reason") or session["message"],
            steps=steps,
            taskId=task_id,
            task_id=task_id,
        )

    # Run execution directly and await completion
    result = await _manager.execute(task_id)

    events = result.get("events", [])
    steps_log = [f"{e.get('title')}: {e.get('details', {}).get('summary', '')}" for e in events if e.get("title")]
    if not steps_log:
        steps_log = [f"Execution status: {result.get('status', 'unknown')}"]

    raw_status = result.get("status", "unknown")
    result_status = result.get("result_status")
    
    # Truthful status mapping (Section A: Never convert failed/not_done into completed)
    if raw_status in {"done", "completed"} and result_status == "done":
        res_status = "completed"
    elif raw_status == "needs_approval":
        res_status = "needs_approval"
    elif raw_status == "stopped":
        res_status = "stopped"
    elif raw_status in {"failed", "not_done"} or result_status == "not_done":
        res_status = "failed" if raw_status == "failed" else "not_done"
    else:
        res_status = raw_status

    summary = result.get("result_summary") or result.get("error")
    if not summary:
        summary = f"Task execution finished with status: {res_status}"

    return AutomationExecuteResponse(
        status=res_status,
        message=summary,
        summary=summary,
        steps=steps_log,
        taskId=task_id,
        task_id=task_id,
    )
