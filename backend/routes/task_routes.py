"""
Task API routes.

POST /api/task/start   — Start an organizer run.
GET  /api/task/{run_id} — Get task/run status.
GET  /api/tasks         — List available organizer tasks.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from schemas.task_schema import (
    TaskStartRequest,
    TaskStartResponse,
    TaskStatusResponse,
    AvailableTasksResponse,
)
from services.portal_service import PortalService, PortalServiceError
from services.task_manager import TaskManager, get_run
from services.security_service import SecurityService
from utils.action_logger import log_action

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/task", tags=["Tasks"])

_portal = PortalService()
_manager = TaskManager()
_security = SecurityService()


@router.post(
    "/start",
    response_model=TaskStartResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a new organizer task run with pre-execution security validation",
)
async def start_task(body: TaskStartRequest):
    """
    Executes the 12-step pre-execution security workflow:
      STEP 1: Receive website_url, username, password, user_prompt, task_id.
      STEP 2: Validate input presence and format.
      STEP 3: Validate website URL and trusted organizer domain.
      STEP 4: Validate credential presence and format (credentials never logged/returned).
      STEP 5: Run prompt security rules.
      STEP 6: Run Logistic Regression prediction.
      STEP 7: Combine security decisions.
      STEP 8: If BLOCKED, stop execution and reject request.
      STEP 9: If REVIEW, request clarification without starting browser execution.
      STEP 10: If SAFE, proceed to official organizer portal integration.
      STEP 11: Start or retrieve the official organizer run using portal service.
      STEP 12: Pass authorized task & validated context to browser service.
    """
    validation_info = None

    # If student provided inputs (URL, username, password, user_prompt)
    if body.website_url or body.user_prompt or body.username or body.password:
        url_to_check = body.website_url or ""
        user_to_check = body.username or ""
        pass_to_check = body.password or ""
        prompt_to_check = body.user_prompt or ""

        validation = _security.validate_pre_execution(
            website_url=url_to_check,
            username=user_to_check,
            password=pass_to_check,
            user_prompt=prompt_to_check,
        )

        validation_info = validation

        # Log security validation event (NO CREDENTIALS)
        log_action(
            run_id=body.task_id or "pre_task_start",
            step=0,
            action="task_start_security_validation",
            target=url_to_check,
            reason=f"overall={validation['overall_status']}",
            result=validation["overall_status"],
        )

        # STEP 8 & 9: If BLOCKED or REVIEW, stop execution
        if not validation["allowed_to_proceed"]:
            reason = (
                validation["prompt_validation"].get("reason")
                or validation["website_validation"].get("reason")
                or validation["credential_validation"].get("reason")
                or f"Task rejected by security policy ({validation['overall_status']})"
            )
            logger.warning(
                "Task start blocked by security: status=%s, reason=%s",
                validation["overall_status"],
                reason,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Task start {validation['overall_status']}: {reason}",
            )

    # STEP 10 & 11: Call organizer portal to start the run
    try:
        result = await _manager.start_task_run(
            task_id=body.task_id,
            website_url=body.website_url,
            username=body.username,
            password=body.password,
            user_prompt=body.user_prompt,
            security_validation=validation_info,
        )
        return TaskStartResponse(**result)
    except PortalServiceError as exc:
        logger.error("Portal error on task start: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Organizer portal error: {exc}",
        )
    except Exception as exc:
        logger.error("Unexpected error on task start: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )


@router.get(
    "/{run_id}",
    response_model=TaskStatusResponse,
    summary="Get task run status",
)
async def get_task_status(run_id: str):
    run = get_run(run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found.",
        )
    return TaskStatusResponse(
        run_id=run_id,
        task_id=run.get("task_id", ""),
        status=run.get("status", "unknown"),
        current_step=run.get("step"),
        progress=run.get("current_action"),
        error=run.get("error"),
    )


@router.get(
    "",
    response_model=AvailableTasksResponse,
    summary="List available organizer practice tasks",
)
async def list_tasks():
    try:
        tasks = await _portal.get_available_tasks()
        return AvailableTasksResponse(tasks=tasks, count=len(tasks))
    except PortalServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not fetch tasks: {exc}",
        )

