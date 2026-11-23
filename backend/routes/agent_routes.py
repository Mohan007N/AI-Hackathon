"""
Agent execution API routes.

POST /api/agent/execute         — Start the AI agent workflow for a run.
GET  /api/agent/status/{run_id} — Get current execution status.
GET  /api/agent/logs/{run_id}   — Get JSONL action log entries.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from schemas.agent_schema import (
    AgentExecuteRequest,
    AgentStatusResponse,
    AgentLogsResponse,
    ActionLogEntry,
)
from services.task_manager import TaskManager, get_run

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/agent", tags=["Agent"])

_manager = TaskManager()


@router.post(
    "/execute",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start the AI agent workflow for a run",
)
async def execute_agent(body: AgentExecuteRequest, background_tasks: BackgroundTasks):
    """
    Starts the full agent workflow asynchronously.
    Use GET /api/agent/status/{run_id} to poll progress.
    """
    run = get_run(body.run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{body.run_id}' not found. Call /api/task/start first.",
        )
    if run.get("status") == "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent is already running for this run.",
        )

    background_tasks.add_task(_manager.execute, body.run_id)

    return {
        "run_id": body.run_id,
        "status": "accepted",
        "message": "Agent workflow started. Poll /api/agent/status/{run_id} for updates.",
    }


@router.get(
    "/status/{run_id}",
    response_model=AgentStatusResponse,
    summary="Get agent execution status",
)
async def agent_status(run_id: str):
    status_data = _manager.get_status(run_id)
    if status_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found.",
        )
    return AgentStatusResponse(
        run_id=run_id,
        status=status_data.get("status", "unknown"),
        current_action=status_data.get("current_action"),
        step=status_data.get("step"),
        error=status_data.get("error"),
    )


@router.get(
    "/logs/{run_id}",
    response_model=AgentLogsResponse,
    summary="Get action log for a run",
)
async def agent_logs(run_id: str):
    logs = _manager.get_logs(run_id)
    entries = [ActionLogEntry(**entry) for entry in logs if "step" in entry and "action" in entry]
    return AgentLogsResponse(run_id=run_id, entries=entries, count=len(entries))

