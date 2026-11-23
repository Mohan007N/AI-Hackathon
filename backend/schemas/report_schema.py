"""
Pydantic schemas for report-related endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


class ReportResponse(BaseModel):
    run_id: str
    task_id: Optional[str] = None
    status: str           # "done" | "not_done"
    summary: str
    actions: list[dict[str, Any]] = Field(default_factory=list)
    submitted_at: Optional[datetime] = None


class ReportSubmitResponse(BaseModel):
    run_id: str
    portal_response: Optional[dict[str, Any]] = None
    success: bool
    message: str
    submitted_at: datetime = Field(default_factory=datetime.now)

