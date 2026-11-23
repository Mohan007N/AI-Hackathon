"""
Pydantic schemas for agent execution endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


class AgentExecuteRequest(BaseModel):
    run_id: str = Field(..., description="Run ID returned by /api/task/start")

    model_config = {"json_schema_extra": {"example": {"run_id": "run_abc123"}}}


class ActionLogEntry(BaseModel):
    t: str                           # ISO-8601 timestamp
    step: int
    action: str
    target: Optional[str] = None
    url: Optional[str] = None
    value: Optional[Any] = None
    reason: Optional[str] = None
    result: Optional[str] = None


class AgentStatusResponse(BaseModel):
    run_id: str
    status: str    # "idle" | "running" | "awaiting_approval" | "done" | "error"
    current_action: Optional[str] = None
    step: Optional[int] = None
    error: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.now)


class AgentLogsResponse(BaseModel):
    run_id: str
    entries: list[ActionLogEntry]
    count: int

