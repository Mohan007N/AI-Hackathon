"""
Report API routes.

GET  /api/report/{run_id}        — Get the final task report.
POST /api/report/{run_id}/submit — Submit report to organizer portal.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from schemas.report_schema import ReportResponse, ReportSubmitResponse
from services.report_service import ReportService
from services.task_manager import get_run

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/report", tags=["Reports"])

_report_service = ReportService()


@router.get(
    "/{run_id}",
    response_model=ReportResponse,
    summary="Get the final task report for a run",
)
async def get_report(run_id: str):
    run = get_run(run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found.",
        )

    report = await _report_service.build_report(
        run_id=run_id,
        task_id=run.get("task_id", ""),
        status=run.get("result_status") or run.get("status", "not_done"),
        summary=run.get("result_summary") or "Task in progress.",
    )
    return ReportResponse(
        run_id=run_id,
        task_id=report["task_id"],
        status=report["status"],
        summary=report["summary"],
        actions=report["actions"],
    )


@router.post(
    "/{run_id}/submit",
    response_model=ReportSubmitResponse,
    summary="Submit final report to organizer portal",
)
async def submit_report(run_id: str):
    run = get_run(run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found.",
        )

    result_status = run.get("result_status") or run.get("status", "not_done")
    if result_status not in {"done", "not_done"}:
        result_status = "not_done"

    result = await _report_service.submit(
        run_id=run_id,
        task_id=run.get("task_id", ""),
        status=result_status,
        summary=run.get("result_summary") or "No summary available.",
    )

    return ReportSubmitResponse(
        run_id=run_id,
        portal_response=result.get("portal_response"),
        success=result["success"],
        message=result["message"],
    )

