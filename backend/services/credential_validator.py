"""
Credential Validator for Safe Web Interactive Agent.

Validates that credentials are present and properly formatted
BEFORE passing them to browser login.

SECURITY CONTRACT:
- Never classify passwords using ML.
- Never store credentials in datasets.
- Never send credentials to OpenRouter / LLM.
- Never log passwords to files or terminal.
- Never include credentials in exception messages.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from config.security_config import SecurityConfig

logger = logging.getLogger(__name__)


@dataclass
class CredentialValidationResult:
    status: str          # "valid" | "invalid"
    reason: str
    username_present: bool = False
    password_present: bool = False
    errors: list[str] | None = None


class CredentialValidator:
    """
    Validates credential presence and format constraints.
    Does NOT verify credentials against any service — actual
    authentication happens only through the browser login form
    on the approved portal.
    """

    def validate(
        self,
        username: str | None,
        password: str | None,
    ) -> CredentialValidationResult:
        """
        Check that username and password are present and within constraints.
        Returns structured result. Never includes credential values.
        """
        errors: list[str] = []
        username_ok = False
        password_ok = False

        # ── Username ──────────────────────────────────────────────────────────
        if not username or not username.strip():
            errors.append("Username is required.")
        elif len(username) > SecurityConfig.MAX_USERNAME_LENGTH:
            errors.append(
                f"Username exceeds maximum length ({SecurityConfig.MAX_USERNAME_LENGTH} chars)."
            )
        else:
            username_ok = True

        # ── Password ──────────────────────────────────────────────────────────
        if not password:
            errors.append("Password is required.")
        elif len(password) > SecurityConfig.MAX_PASSWORD_LENGTH:
            errors.append(
                f"Password exceeds maximum length ({SecurityConfig.MAX_PASSWORD_LENGTH} chars)."
            )
        else:
            password_ok = True

        # ── Result ────────────────────────────────────────────────────────────
        if errors:
            # Log that validation failed without revealing values
            logger.warning("Credential validation failed: %d error(s).", len(errors))
            return CredentialValidationResult(
                status="invalid",
                reason="; ".join(errors),
                username_present=username_ok,
                password_present=password_ok,
                errors=errors,
            )

        return CredentialValidationResult(
            status="valid",
            reason="Credentials present and within constraints.",
            username_present=True,
            password_present=True,
        )

