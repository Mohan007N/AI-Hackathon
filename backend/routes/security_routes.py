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
)
from services.url_validator import URLValidator
from services.credential_validator import CredentialValidator
from services.prompt_validator import PromptValidator
from services.portal_service import PortalService, PortalServiceError
from services.task_manager import TaskManager
from utils.action_logger import log_action

from fastapi import HTTPException

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/security", tags=["Security"])

_url_validator = URLValidator()
_cred_validator = CredentialValidator()
_prompt_validator = PromptValidator()
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
    # ── Step 1: URL ──────────────────────────────────────────────────────────
    url_result = _url_validator.validate(website_url)

    # ── Step 2: Credentials ──────────────────────────────────────────────────
    cred_result = _cred_validator.validate(username, password)

    # ── Step 3: Prompt ───────────────────────────────────────────────────────
    prompt_result = _prompt_validator.validate(user_prompt)

    # ── Step 4: Combine decision ─────────────────────────────────────────────
    # Priority: blocked > review > safe
    statuses = [url_result.status, prompt_result.status]
    if cred_result.status == "invalid":
        statuses.append("blocked")

    if "blocked" in statuses or prompt_result.status == "malicious":
        overall = "blocked"
    elif "review" in statuses or prompt_result.status == "uncertain":
        overall = "review"
    else:
        overall = "safe"

    allowed = overall == "safe"

    return SecurityValidateResponse(
        overall_status=overall,
        allowed_to_proceed=allowed,
        website_validation=WebsiteValidationResult(
            status=url_result.status,
            reason=url_result.reason,
            domain=url_result.domain,
            is_trusted=url_result.is_trusted,
        ),
        credential_validation=CredValResult(
            status=cred_result.status,
            reason=cred_result.reason if cred_result.status == "invalid" else None,
        ),
        prompt_validation=PromptValidationResultSchema(
            status=prompt_result.status,
            reason=prompt_result.reason,
            confidence=prompt_result.confidence,
        ),
        security_checks=SecurityChecksResult(
            rule_based=prompt_result.rule_based,
            ml_classifier=prompt_result.ml_classifier,
        ),
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

