"""
Security configuration for Safe Web Interactive Agent.

Centralizes all security policy constants, trusted domains,
task categories, and URL validation settings.

Uses environment variables for trusted domain configuration.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse
from config.settings import settings


class SecurityConfig:
    """Immutable security policy configuration."""

    # ── Trusted portal ────────────────────────────────────────────────────────
    # The organizer-provided portal URL is the single source of truth.
    # Additional trusted domains can be added via env var (comma-separated).
    PORTAL_URL: str = settings.PORTAL_URL
    TRUSTED_DOMAINS: list[str] = [
        "college-portal.example.com",
        "drbexams.net",
        "www.drbexams.net",
    ]

    @classmethod
    def get_trusted_domains(cls) -> list[str]:
        """Return list of trusted domains derived from PORTAL_URL and env."""
        domains = list(cls.TRUSTED_DOMAINS)

        # Check current env and settings for portal URL dynamically
        for portal_cand in [
            os.getenv("PORTAL_URL", ""),
            getattr(settings, "PORTAL_URL", ""),
            cls.PORTAL_URL,
        ]:
            if portal_cand:
                try:
                    parsed = urlparse(portal_cand)
                    if parsed.hostname:
                        domains.append(parsed.hostname.lower())
                except Exception:
                    pass

        # Additional trusted domains from env (comma-separated)
        extra = os.getenv("TRUSTED_DOMAINS", "")
        if extra:
            for d in extra.split(","):
                d = d.strip().lower()
                if d:
                    domains.append(d)
        return list(set(domains))

    # ── HTTPS requirement ─────────────────────────────────────────────────────
    REQUIRE_HTTPS: bool = True

    # ── Redirect policy ───────────────────────────────────────────────────────
    MAX_REDIRECTS: int = 3
    REVALIDATE_REDIRECTS: bool = True

    # ── Prompt constraints ────────────────────────────────────────────────────
    MAX_PROMPT_LENGTH: int = 2000
    MAX_USERNAME_LENGTH: int = 150
    MAX_PASSWORD_LENGTH: int = 256
    MAX_URL_LENGTH: int = 2048

    # ── Supported task categories ─────────────────────────────────────────────
    SUPPORTED_TASK_CATEGORIES: list[str] = [
        "attendance",
        "hall_ticket",
        "timetable",
        "elective_registration",
        "leave_application",
        "fee_management",
        "profile_management",
        "academic_results",
        "general_portal_query",
    ]

    # ── Suspicious domain indicators ──────────────────────────────────────────
    SUSPICIOUS_DOMAIN_KEYWORDS: list[str] = [
        "login", "signin", "account", "verify", "secure",
        "update", "confirm", "auth", "bank", "paypal",
    ]

    # ── Blocked URL schemes ───────────────────────────────────────────────────
    BLOCKED_SCHEMES: set[str] = {
        "javascript", "data", "ftp", "file", "telnet", "ssh",
        "mailto", "blob",
    }

    # ── Blocked ports (non-standard suspicious) ───────────────────────────────
    ALLOWED_PORTS: set[int | None] = {None, 80, 443, 8080, 8443}

