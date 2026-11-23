"""
Approval API routes.

POST /api/approval/respond — Accept frontend approval response.

IMPORTANT: Frontend approval is a convenience UI only.
           The official confirm.py result is always the binding gate.
           This endpoint records the student's intent but does NOT
           execute irreversible actions on its own.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from schemas.approval_schema import ApprovalRespondRequest, ApprovalRespondResponse
from services.approval_service import ApprovalService
from services.task_manager import get_run
from utils.action_logger import log_action

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/approval", tags=["Approval"])

_approval = ApprovalService()


@router.post(
    "/respond",
    response_model=ApprovalRespondResponse,
    summary="Submit frontend approval response for an irreversible action",
)
async def approval_respond(body: ApprovalRespondRequest):
    """
    Records the student's frontend approval/denial.
    Then calls the official confirm.py function as the binding gate.
    Returns the final decision.
    """
    run = get_run(body.run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{body.run_id}' not found.",
        )

    # Log frontend decision (never executes action itself)
    log_action(
        body.run_id,
        run.get("step", 0) + 1,
        "frontend_approval_received",
        target=body.action_description,
        value=body.approved,
        reason=body.student_comment or "Student responded via UI",
    )

    if not body.approved:
        log_action(
            body.run_id,
            run.get("step", 0) + 2,
            "action_denied_by_student",
            target=body.action_description,
            reason="Student denied via frontend",
        )
        return ApprovalRespondResponse(
            run_id=body.run_id,
            action_description=body.action_description,
            frontend_approved=False,
            official_confirmed=None,
            final_decision="denied",
            message="Action denied by student via frontend interface.",
        )

    # Student approved via frontend — still must call official confirm.py
    portal_url = run.get("portal_url", "")
    confirm_result = await _approval.request_confirmation(
        body.action_description,
        portal_url,
        body.run_id,
    )
    official_confirmed = confirm_result["approved"]

    log_action(
        body.run_id,
        run.get("step", 0) + 3,
        "official_confirmation_result",
        target=body.action_description,
        value=official_confirmed,
        reason=confirm_result.get("message"),
    )

    final_decision = "approved" if official_confirmed else "denied"
    return ApprovalRespondResponse(
        run_id=body.run_id,
        action_description=body.action_description,
        frontend_approved=True,
        official_confirmed=official_confirmed,
        final_decision=final_decision,
        message=confirm_result.get("message", ""),
    )

