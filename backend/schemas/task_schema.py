"""
Pydantic schemas for task-related API endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


# ── Request Models ──────────────────────────────────────────────────────────

class TaskStartRequest(BaseModel):
    task_id: str = Field("p01", description="Organizer-assigned task identifier, e.g. 'p01'")
    website_url: Optional[str] = Field(None, description="College portal website URL (e.g. https://college-portal.example.com)")
    username: Optional[str] = Field(None, description="College student username (never returned in responses or logged)")
    password: Optional[str] = Field(None, description="College student password (never returned in responses or logged)")
    user_prompt: Optional[str] = Field(None, description="Student's natural-language task prompt")

    model_config = {
        "json_schema_extra": {
            "example": {
                "task_id": "p01",
                "website_url": "https://college-portal.example.com",
                "username": "student123",
                "password": "example-password",
                "user_prompt": "Check my attendance and download my hall ticket",
            }
        }
    }


# ── Response Models ─────────────────────────────────────────────────────────

class TaskInfo(BaseModel):
    task_id: str
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    difficulty: Optional[str] = None


class TaskStartResponse(BaseModel):
    run_id: Optional[str] = None
    task_id: str
    portal_url: Optional[str] = None
    request_text: Optional[str] = None
    status: str = "started"  # "started" | "blocked" | "review"
    message: Optional[str] = None
    security_validation: Optional[dict[str, Any]] = None
    started_at: datetime = Field(default_factory=datetime.now)


class TaskStatusResponse(BaseModel):
    run_id: str
    task_id: str
    status: str  # "started" | "running" | "awaiting_approval" | "done" | "not_done" | "error"
    progress: Optional[str] = None
    current_step: Optional[int] = None
    total_steps: Optional[int] = None
    error: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.now)


class AvailableTasksResponse(BaseModel):
    tasks: list[dict[str, Any]]
    count: int

