"""
URL Security Validator for Safe Web Interactive Agent.

Validates user-submitted website URLs BEFORE browser launch.
Trust is based on the organizer portal domain — not arbitrary HTTPS sites.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from config.security_config import SecurityConfig

logger = logging.getLogger(__name__)


@dataclass
class URLValidationResult:
    status: str           # "safe" | "review" | "blocked"
    reason: str
    url: str
    domain: Optional[str] = None
    is_trusted: bool = False
    checks_passed: list[str] | None = None
    checks_failed: list[str] | None = None


class URLValidator:
    """
    Validates website URLs against the security policy.
    Only the organizer portal domain and explicitly trusted domains are
    considered safe. All others require review or are blocked outright.
    """

    def __init__(self):
        self._trusted = [d.lower() for d in SecurityConfig.get_trusted_domains()]

    def validate(self, url: str) -> URLValidationResult:
        """Run all URL checks and return a combined result."""
        passed: list[str] = []
        failed: list[str] = []

        # ── 1. Basic format ───────────────────────────────────────────────────
        if not url or not url.strip():
            return URLValidationResult(
                status="blocked", reason="URL is empty.", url=url,
                checks_failed=["format"],
            )

        url = url.strip()
        if len(url) > SecurityConfig.MAX_URL_LENGTH:
            return URLValidationResult(
                status="blocked", reason=f"URL exceeds maximum length ({SecurityConfig.MAX_URL_LENGTH}).",
                url=url, checks_failed=["length"],
            )

        try:
            parsed = urlparse(url)
        except Exception:
            return URLValidationResult(
                status="blocked", reason="URL cannot be parsed.",
                url=url, checks_failed=["parse"],
            )

        # ── 3. Hostname present ──────────────────────────────────────────────
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return URLValidationResult(
                status="blocked", reason="URL has no hostname.",
                url=url, checks_failed=["hostname"],
            )

        is_private = self._is_local_or_private_ip(hostname)
        is_trusted = self._is_trusted_domain(hostname)

        # ── 2. Scheme ────────────────────────────────────────────────────────
        scheme = (parsed.scheme or "").lower()
        if scheme in SecurityConfig.BLOCKED_SCHEMES:
            return URLValidationResult(
                status="blocked", reason=f"Blocked URL scheme: '{scheme}'.",
                url=url, checks_failed=["scheme"],
            )

        if SecurityConfig.REQUIRE_HTTPS and scheme != "https":
            if scheme == "http":
                if not (is_trusted or is_private):
                    failed.append("https_required")
                else:
                    passed.append("http_allowed_for_trusted_portal")
            else:
                return URLValidationResult(
                    status="blocked", reason=f"Invalid or missing URL scheme: '{scheme}'. HTTPS required.",
                    url=url, checks_failed=["scheme"],
                )
        elif scheme in {"https", "http"}:
            passed.append("scheme")
        else:
            return URLValidationResult(
                status="blocked", reason=f"Invalid or missing URL scheme: '{scheme}'. HTTP or HTTPS required.",
                url=url, checks_failed=["scheme"],
            )

        # ── 4. Embedded credentials ──────────────────────────────────────────
        if parsed.username or parsed.password:
            return URLValidationResult(
                status="blocked",
                reason="URL contains embedded username/password — potential phishing.",
                url=url, domain=hostname, checks_failed=["embedded_credentials"],
            )
        passed.append("no_embedded_credentials")

        # ── 5. IP-address URL ────────────────────────────────────────────────
        if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", hostname):
            if is_trusted:
                passed.append("trusted_ip_portal")
            else:
                return URLValidationResult(
                    status="blocked",
                    reason="IP-address-based URL not allowed. Use a domain name or trusted portal IP.",
                    url=url, domain=hostname, checks_failed=["ip_address"],
                )
        else:
            passed.append("not_ip_address")

        # ── 6. Port check (Requirement 9: catch invalid port parsing) ────────
        try:
            port = parsed.port
        except ValueError as port_err:
            return URLValidationResult(
                status="blocked",
                reason=f"Invalid URL port out of range (1-65535): {port_err}",
                url=url, domain=hostname, checks_failed=["invalid_port"],
            )

        if port is not None:
            if not (1 <= port <= 65535):
                return URLValidationResult(
                    status="blocked",
                    reason=f"Port {port} is out of valid range (1-65535).",
                    url=url, domain=hostname, checks_failed=["port_range"],
                )
            if port not in SecurityConfig.ALLOWED_PORTS:
                if is_trusted or is_private:
                    passed.append(f"dev_port_{port}")
                else:
                    failed.append(f"non_standard_port_{port}")
            else:
                passed.append("port")
        else:
            passed.append("default_port")

        # ── 7. Suspicious domain keywords ────────────────────────────────────
        suspicious_hits = [
            kw for kw in SecurityConfig.SUSPICIOUS_DOMAIN_KEYWORDS
            if kw in hostname
        ]
        if suspicious_hits:
            failed.append(f"suspicious_domain_keywords({','.join(suspicious_hits)})")
        else:
            passed.append("domain_keywords")

        # ── 8. Lookalike / homoglyph ─────────────────────────────────────────
        lookalike_check = self._check_lookalike(hostname)
        if lookalike_check:
            failed.append(f"lookalike({lookalike_check})")
        else:
            passed.append("no_lookalike")

        # ── 9. Trusted domain match ──────────────────────────────────────────
        is_trusted = self._is_trusted_domain(hostname)

        if is_trusted:
            passed.append("trusted_domain")
            # Trusted + all critical checks passed → SAFE
            if not failed:
                return URLValidationResult(
                    status="safe",
                    reason="Approved organizer portal domain.",
                    url=url, domain=hostname, is_trusted=True,
                    checks_passed=passed, checks_failed=failed,
                )
            else:
                # Trusted but minor warnings
                return URLValidationResult(
                    status="safe",
                    reason=f"Trusted domain with warnings: {', '.join(failed)}.",
                    url=url, domain=hostname, is_trusted=True,
                    checks_passed=passed, checks_failed=failed,
                )
        else:
            # Not a trusted domain
            if failed:
                return URLValidationResult(
                    status="blocked",
                    reason=f"Untrusted domain '{hostname}' with security warnings: {', '.join(failed)}.",
                    url=url, domain=hostname, is_trusted=False,
                    checks_passed=passed, checks_failed=failed,
                )
            else:
                return URLValidationResult(
                    status="review",
                    reason=(
                        f"Domain '{hostname}' is not in the trusted list. "
                        "Explicit authorization required."
                    ),
                    url=url, domain=hostname, is_trusted=False,
                    checks_passed=passed, checks_failed=failed,
                )

    def validate_redirect(self, original_url: str, redirect_url: str) -> URLValidationResult:
        """Revalidate a redirect destination against the same policy (Requirement 11)."""
        result = self.validate(redirect_url)
        orig_host = (urlparse(original_url).hostname or "").lower()
        redir_host = (urlparse(redirect_url).hostname or "").lower()

        # If redirect goes to an untrusted domain or cross-domain, block or flag for review
        if orig_host and redir_host and orig_host != redir_host:
            if not self._is_trusted_domain(redir_host):
                return URLValidationResult(
                    status="blocked",
                    reason=f"Cross-domain redirect from '{orig_host}' to untrusted domain '{redir_host}' is blocked.",
                    url=redirect_url,
                    domain=redir_host,
                    is_trusted=False,
                )
            elif result.status == "safe":
                result.status = "review"
                result.reason = f"Redirect from '{orig_host}' to '{redir_host}' — cross-domain redirect requires review."
        return result

    # ── Internal ─────────────────────────────────────────────────────────────

    @staticmethod
    def _is_local_or_private_ip(hostname: str) -> bool:
        """Check if hostname is localhost, loopback, or private IPv4 address (RFC 1918)."""
        if hostname in {"localhost", "127.0.0.1"}:
            return True
        match = re.match(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$", hostname)
        if match:
            first = int(match.group(1))
            second = int(match.group(2))
            if first == 10:
                return True
            if first == 127:
                return True
            if first == 192 and second == 168:
                return True
            if first == 172 and 16 <= second <= 31:
                return True
        return False

    def _is_trusted_domain(self, hostname: str) -> bool:
        """Check if hostname matches or is an approved subdomain of a trusted domain (Requirement 10)."""
        trusted_domains = [d.lower() for d in SecurityConfig.get_trusted_domains()]
        for td in trusted_domains:
            # Exact match is always valid
            if hostname == td:
                return True
            # Never permit subdomains of localhost, loopback, or raw IPs (e.g. evil.localhost)
            if td in {"localhost", "127.0.0.1"} or re.match(r"^\d{1,3}(\.\d{1,3}){3}$", td):
                continue
            # For registered domain names, check exact subdomain structure
            if hostname.endswith("." + td):
                return True
        return False

    def _check_lookalike(self, hostname: str) -> Optional[str]:
        """Detect common homoglyph/lookalike patterns."""
        # Common substitutions used in phishing
        lookalike_map = {
            "0": "o", "1": "l", "rn": "m",
            "vv": "w", "cl": "d",
        }
        for fake, real in lookalike_map.items():
            if fake in hostname:
                # Check if replacing the fake char yields a trusted domain
                candidate = hostname.replace(fake, real)
                if self._is_trusted_domain(candidate):
                    return f"'{hostname}' looks like '{candidate}'"
        return None

