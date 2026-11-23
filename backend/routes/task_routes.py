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

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/task", tags=["Tasks"])

_portal = PortalService()
_manager = TaskManager()


@router.post(
    "/start",
    response_model=TaskStartResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a new organizer task run",
)
async def start_task(body: TaskStartRequest):
    """
    Calls the organizer portal to create a new run, then fetches the task request.
    Returns the run_id required for all subsequent calls.
    """
    try:
        result = await _manager.start_task_run(body.task_id)
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

