"""
Security routes — pre-execution validation endpoint.

POST /api/security/validate — Validate URL + credentials + prompt before agent runs.

SECURITY: Never return password or username in any response body.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, status

from schemas.security_schema import (
    SecurityValidateRequest,
    SecurityValidateResponse,
    WebsiteValidationResult,
    CredentialValidationResult as CredValResult,
    PromptValidationResultSchema,
    SecurityChecksResult,
    TaskStartWithInputsRequest,
    TaskStartWithValidationResponse,
    PredictRequest,
    PredictResponse,
)
from services.security_service import SecurityService
from services.portal_service import PortalService, PortalServiceError
from services.task_manager import TaskManager
from utils.action_logger import log_action

from fastapi import HTTPException

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/security", tags=["Security"])

_security = SecurityService()
_portal = PortalService()
_manager = TaskManager()


def _run_validation(
    website_url: str,
    username: str,
    password: str,
    user_prompt: str,
) -> SecurityValidateResponse:
    """
    Core validation logic shared by /api/security/validate and /api/task/start.
    Returns the full SecurityValidateResponse.
    """
    raw = _security.validate_pre_execution(
        website_url=website_url,
        username=username,
        password=password,
        user_prompt=user_prompt,
    )

    return SecurityValidateResponse(
        overall_status=raw["overall_status"],
        allowed_to_proceed=raw["allowed_to_proceed"],
        website_validation=WebsiteValidationResult(**raw["website_validation"]),
        credential_validation=CredValResult(**raw["credential_validation"]),
        prompt_validation=PromptValidationResultSchema(**raw["prompt_validation"]),
        security_checks=SecurityChecksResult(**raw["security_checks"]),
    )


# ── Standalone validation endpoint ───────────────────────────────────────────

@router.post(
    "/validate",
    response_model=SecurityValidateResponse,
    summary="Pre-execution security validation",
    description=(
        "Validates website URL, credential presence, and task prompt safety "
        "BEFORE any browser or AI agent execution. Never returns credentials."
    ),
)
async def validate_security(body: SecurityValidateRequest):
    result = _run_validation(
        body.website_url,
        body.username,
        body.password,
        body.user_prompt,
    )

    # Log the validation event (no credentials!)
    log_action(
        run_id=body.task_id or "pre_validation",
        step=0,
        action="security_validation",
        target=body.website_url,
        reason=f"overall={result.overall_status}",
        result=result.overall_status,
    )

    return result


# ── Standalone ML Predict Endpoint ───────────────────────────────────────────

@router.post(
    "/predict",
    response_model=PredictResponse,
    summary="Predict prompt safety using trained ML classifier",
    description="Returns risk classification (legitimate, malicious, uncertain), confidence score, and class probabilities.",
)
async def predict_prompt_security(body: PredictRequest):
    predictor = _security._get_predictor()
    if not predictor or not predictor.is_ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trained ML model artifacts not found. Run python ml/train_classifier.py first.",
        )
    result = predictor.predict(body.text)
    return PredictResponse(
        text=body.text,
        label=result.get("label", "uncertain"),
        confidence=result.get("confidence", 0.0),
        model=result.get("model", "logistic_regression"),
        probabilities=result.get("probabilities", {}),
    )


# ── Extended task start with pre-validation ──────────────────────────────────

@router.post(
    "/task/start",
    response_model=TaskStartWithValidationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start task with full security pre-validation",
    description=(
        "Receives all user inputs, runs security validation, then starts "
        "the organizer portal run only if validation passes."
    ),
)
async def start_task_with_validation(body: TaskStartWithInputsRequest):
    """
    1. Validate URL.
    2. Validate credential presence.
    3. Validate prompt (rule-based + ML).
    4. STOP if blocked or uncertain.
    5. Start organizer run only after all checks pass.
    """
    validation = _run_validation(
        body.website_url,
        body.username,
        body.password,
        body.user_prompt,
    )

    # Log validation
    log_action(
        run_id=body.task_id,
        step=0,
        action="pre_start_security_validation",
        target=body.website_url,
        reason=f"overall={validation.overall_status}",
        result=validation.overall_status,
    )

    if not validation.allowed_to_proceed:
        return TaskStartWithValidationResponse(
            task_id=body.task_id,
            security_validation=validation,
            status=validation.overall_status,
            message=f"Security validation {validation.overall_status}: {validation.prompt_validation.reason or validation.website_validation.reason}",
        )

    # ── All checks passed — start the organizer run ──────────────────────────
    try:
        run_result = await _manager.start_task_run(body.task_id)
    except PortalServiceError as exc:
        logger.error("Portal error on task start: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Organizer portal error: {exc}",
        )

    # Store the validated user inputs in the run context (credentials in-memory only)
    from services.task_manager import _update_run
    _update_run(run_result["run_id"],
                _website_url=body.website_url,
                _username=body.username,
                _password=body.password,
                user_prompt=body.user_prompt)

    return TaskStartWithValidationResponse(
        run_id=run_result["run_id"],
        task_id=body.task_id,
        security_validation=validation,
        status="started",
        message="Security validation passed. Task run started.",
        started_at=run_result.get("started_at"),
    )

