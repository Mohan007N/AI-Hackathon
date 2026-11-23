"""
Pydantic schemas for approval-related endpoints.

IMPORTANT: The frontend approval interface is a convenience layer only.
           The official organiser confirm.py result is always the final
           enforcement gate before any irreversible action executes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class ApprovalRespondRequest(BaseModel):
    run_id: str = Field(..., description="Run ID awaiting confirmation")
    action_description: str = Field(..., description="Exact action text shown to the student")
    approved: bool = Field(..., description="True = student approved, False = student denied")
    student_comment: Optional[str] = Field(None, description="Optional free-text comment from student")

    model_config = {
        "json_schema_extra": {
            "example": {
                "run_id": "run_abc123",
                "action_description": "Register for OE123 - Data Science Fundamentals",
                "approved": True,
                "student_comment": "Yes, please register.",
            }
        }
    }


class ApprovalRespondResponse(BaseModel):
    run_id: str
    action_description: str
    frontend_approved: bool
    official_confirmed: Optional[bool] = None   # filled after confirm.py call
    final_decision: str                          # "approved" | "denied" | "pending_official"
    message: str
    timestamp: datetime = Field(default_factory=datetime.now)

