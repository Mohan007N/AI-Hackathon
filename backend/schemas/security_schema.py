"""
Pydantic schemas for security validation endpoints.

POST /api/security/validate

SECURITY: Never return submitted username or password in responses.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field, SecretStr


# ── Request ──────────────────────────────────────────────────────────────────

class SecurityValidateRequest(BaseModel):
    website_url: str = Field(..., description="College portal URL to validate")
    username: str = Field(..., description="Student username (never returned in response)")
    password: str = Field(..., description="Student password (never logged or returned)")
    user_prompt: str = Field(..., description="Student's natural-language task prompt")
    task_id: Optional[str] = Field(None, description="Organizer task ID if available")

    model_config = {
        "json_schema_extra": {
            "example": {
                "website_url": "https://college-portal.example.com",
                "username": "student123",
                "password": "••••••••",
                "user_prompt": "Check my attendance and download my hall ticket",
                "task_id": "p01",
            }
        }
    }


# ── Sub-results ──────────────────────────────────────────────────────────────

class WebsiteValidationResult(BaseModel):
    status: str           # "safe" | "review" | "blocked"
    reason: str
    domain: Optional[str] = None
    is_trusted: bool = False


class CredentialValidationResult(BaseModel):
    status: str           # "valid" | "invalid"
    reason: Optional[str] = None


class PromptValidationResultSchema(BaseModel):
    status: str           # "legitimate" | "malicious" | "uncertain"
    reason: Optional[str] = None
    confidence: Optional[float] = None


class SecurityChecksResult(BaseModel):
    rule_based: str       # "passed" | "blocked" | "review"
    ml_classifier: str    # "passed" | "blocked" | "review" | "unavailable"


# ── Response ─────────────────────────────────────────────────────────────────

class SecurityValidateResponse(BaseModel):
    overall_status: str           # "safe" | "review" | "blocked"
    allowed_to_proceed: bool
    website_validation: WebsiteValidationResult
    credential_validation: CredentialValidationResult
    prompt_validation: PromptValidationResultSchema
    security_checks: SecurityChecksResult
    timestamp: datetime = Field(default_factory=datetime.now)

    model_config = {
        "json_schema_extra": {
            "example": {
                "overall_status": "safe",
                "allowed_to_proceed": True,
                "website_validation": {
                    "status": "safe",
                    "reason": "Approved organizer portal domain",
                    "domain": "college-portal.example.com",
                    "is_trusted": True,
                },
                "credential_validation": {"status": "valid"},
                "prompt_validation": {
                    "status": "legitimate",
                    "confidence": 0.97,
                },
                "security_checks": {
                    "rule_based": "passed",
                    "ml_classifier": "passed",
                },
            }
        }
    }


# ── Extended TaskStartRequest with user inputs ──────────────────────────────

class TaskStartWithInputsRequest(BaseModel):
    """Extended task start that includes student inputs for pre-validation."""
    task_id: str = Field(..., description="Organizer-assigned task identifier")
    website_url: str = Field(..., description="College portal URL")
    username: str = Field(..., description="Student username")
    password: str = Field(..., description="Student password")
    user_prompt: str = Field(..., description="Student's task prompt")

    model_config = {
        "json_schema_extra": {
            "example": {
                "task_id": "p01",
                "website_url": "https://college-portal.example.com",
                "username": "student123",
                "password": "••••••••",
                "user_prompt": "Check my attendance",
            }
        }
    }


class TaskStartWithValidationResponse(BaseModel):
    """Response when task start includes security validation results."""
    run_id: Optional[str] = None
    task_id: str
    security_validation: SecurityValidateResponse
    status: str             # "started" | "blocked" | "review"
    message: str
    started_at: Optional[datetime] = None


# ── ML Predict Schemas ──────────────────────────────────────────────────────

class PredictRequest(BaseModel):
    text: str = Field(..., description="Prompt or webpage text to classify for prompt security risk")

    model_config = {
        "json_schema_extra": {
            "example": {
                "text": "Check my attendance for this semester"
            }
        }
    }


class PredictResponse(BaseModel):
    text: str
    label: str               # "legitimate" | "malicious" | "uncertain"
    confidence: float
    model: str = "logistic_regression"
    probabilities: dict[str, float] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.now)

