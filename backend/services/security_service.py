"""
Security service — hybrid prompt-injection detection & pre-execution validator.

Combines:
  1. Deterministic regex rules (utils/security_rules.py) — always applied first and final authority.
  2. Trained Logistic Regression ML classifier (ml/predictor.py) — provides risk signals.
  3. Website URL validator (services/url_validator.py) — domain allowlist and HTTPS enforcement.
  4. Credential validator (services/credential_validator.py) — format and safety constraints.

Supports both pre-execution user input validation and post-login DOM webpage inspection.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from utils.security_rules import SECURITY_RULES, RuleSeverity, SecurityRule
from services.url_validator import URLValidator
from services.credential_validator import CredentialValidator
from services.prompt_validator import PromptValidator

logger = logging.getLogger(__name__)


@dataclass
class SecurityResult:
    classification: str          # "safe" | "malicious" | "uncertain"
    is_safe: bool
    reason: str
    matched_rules: list[str] = field(default_factory=list)
    severity: Optional[str] = None
    ml_label: Optional[str] = None
    ml_confidence: Optional[float] = None


class SecurityService:
    """
    Evaluates:
      1. Pre-execution inputs (URL, Credentials, Prompt).
      2. Post-login Webpage content for indirect prompt-injection.
      3. Proposed browser actions against the original student task.
    """

    def __init__(self):
        self._url_validator = URLValidator()
        self._cred_validator = CredentialValidator()
        self._prompt_validator = PromptValidator()
        self._ml_predictor = None
        self._ml_loaded = False

    def _get_predictor(self):
        if not self._ml_loaded:
            self._ml_loaded = True
            try:
                from ml.predictor import PromptPredictor
                self._ml_predictor = PromptPredictor()
            except Exception as exc:
                logger.warning("ML predictor could not be loaded in SecurityService: %s", exc)
                self._ml_predictor = None
        return self._ml_predictor

    # ── Pre-execution Validation (URL + Credentials + Prompt Rules + ML) ──────

    def validate_pre_execution(
        self,
        website_url: str,
        username: str,
        password: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        """
        Executes Steps 1 to 7 of the required security workflow:
          - Step 2: Validate website URL and domain trust.
          - Step 4: Validate credential presence and format constraints.
          - Step 5: Run deterministic prompt security rules.
          - Step 6: Run Logistic Regression prediction.
          - Step 7: Combine security results into SAFE, REVIEW, or BLOCKED.

        Credentials are NEVER returned, logged, or sent to ML/LLM.
        """
        url_res = self._url_validator.validate(website_url)
        cred_res = self._cred_validator.validate(username, password)
        prompt_res = self._prompt_validator.validate(user_prompt)

        # Synthesize decision
        statuses = [url_res.status, prompt_res.status]
        if cred_res.status == "invalid":
            statuses.append("blocked")

        if "blocked" in statuses or prompt_res.status == "malicious":
            overall = "blocked"
        elif "review" in statuses or prompt_res.status == "uncertain":
            overall = "review"
        else:
            overall = "safe"

        allowed = overall == "safe"

        return {
            "overall_status": overall,
            "allowed_to_proceed": allowed,
            "website_validation": {
                "status": url_res.status,
                "reason": url_res.reason,
                "domain": url_res.domain,
                "is_trusted": url_res.is_trusted,
            },
            "credential_validation": {
                "status": cred_res.status,
                "reason": cred_res.reason if cred_res.status == "invalid" else None,
            },
            "prompt_validation": {
                "status": prompt_res.status,
                "reason": prompt_res.reason,
                "confidence": prompt_res.confidence,
                "ml_label": prompt_res.ml_label,
            },
            "security_checks": {
                "rule_based": prompt_res.rule_based,
                "ml_classifier": prompt_res.ml_classifier,
            },
        }

    # ── Webpage Content Inspection (Dual-layer: Rules + ML) ───────────────────

    def check_webpage_content(
        self, content: str, student_task: str
    ) -> SecurityResult:
        """
        Inspect raw webpage text extracted from the portal for indirect prompt injection.
        Treats all webpage text as UNTRUSTED.

        Layer 1: Deterministic regex rules.
        Layer 2: Logistic Regression classifier risk assessment.
        """
        # 1. Deterministic rules check first
        rule_result = self._run_rules(content, context="webpage")
        if not rule_result.is_safe:
            return rule_result

        # 2. ML classifier check on webpage text (sliding window chunks)
        predictor = self._get_predictor()
        if predictor and predictor.is_ready:
            try:
                chunk_size = 1000
                overlap = 200
                step = max(1, chunk_size - overlap)
                chunks = []
                if len(content) <= chunk_size:
                    chunks = [content]
                else:
                    for i in range(0, len(content), step):
                        c = content[i : i + chunk_size]
                        if c.strip():
                            chunks.append(c)

                uncertain_result = None
                for idx, chunk in enumerate(chunks):
                    pred = predictor.predict(chunk)
                    label = pred.get("label")
                    conf = pred.get("confidence", 0.0)

                    if label == "malicious" and conf >= 0.80:
                        logger.warning(
                            "ML classifier flagged webpage chunk %d as malicious (confidence: %.2f)",
                            idx,
                            conf,
                        )
                        return SecurityResult(
                            classification="malicious",
                            is_safe=False,
                            reason=f"ML classifier detected indirect prompt injection in webpage ({conf:.0%} confidence)",
                            matched_rules=["ML-INJ-001"],
                            severity=RuleSeverity.HIGH.value,
                            ml_label=label,
                            ml_confidence=conf,
                        )
                    elif label == "uncertain" and not uncertain_result:
                        uncertain_result = SecurityResult(
                            classification="uncertain",
                            is_safe=False,
                            reason="Webpage content flagged as ambiguous by security model",
                            matched_rules=["ML-UNC-001"],
                            severity=RuleSeverity.MEDIUM.value,
                            ml_label=label,
                            ml_confidence=conf,
                        )

                if uncertain_result:
                    return uncertain_result
            except Exception as exc:
                logger.debug("Webpage ML check skipped due to error: %s", exc)

        return rule_result

    # ── Proposed Action Validation ────────────────────────────────────────────

    def check_proposed_action(
        self,
        action_description: str,
        student_task: str,
        source: str = "llm",
    ) -> SecurityResult:
        """
        Check a proposed action string (from LLM or any internal module).
        Validates that the action is consistent with the student's original task.
        """
        ignore_rules: set[str] = set()
        task_lower = student_task.lower()
        if any(w in task_lower for w in ["pay", "payment"]):
            ignore_rules.add("INJ-005")
        if any(w in task_lower for w in ["drop", "deregister", "cancel", "remove"]):
            ignore_rules.add("INJ-009")

        result = self._run_rules(
            action_description,
            context=f"proposed_action({source})",
            ignore_rules=ignore_rules,
        )
        if result.is_safe:
            # Additional: check task alignment
            result = self._check_task_alignment(action_description, student_task, result)
        return result

    def check_action_type(self, action_type: str, student_task: str) -> SecurityResult:
        """Check whether an action type string is in the allowed whitelist."""
        from utils.security_rules import ALLOWED_ACTION_TYPES

        if action_type not in ALLOWED_ACTION_TYPES:
            return SecurityResult(
                classification="malicious",
                is_safe=False,
                reason=f"Action type '{action_type}' is not in the allowed whitelist.",
                matched_rules=["WHITELIST-001"],
                severity=RuleSeverity.CRITICAL.value,
            )
        return SecurityResult(classification="safe", is_safe=True, reason="Action type whitelisted.")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _run_rules(
        self,
        text: str,
        context: str,
        ignore_rules: set[str] | None = None,
    ) -> SecurityResult:
        ignore_rules = ignore_rules or set()
        matched: list[SecurityRule] = []
        for rule in SECURITY_RULES:
            if rule.rule_id in ignore_rules:
                continue
            for pattern in rule.patterns:
                if pattern.search(text):
                    matched.append(rule)
                    break   # one pattern match per rule is enough

        if not matched:
            return SecurityResult(
                classification="safe",
                is_safe=True,
                reason="No security rules triggered.",
            )

        # Find worst severity
        severity_order = [
            RuleSeverity.LOW, RuleSeverity.MEDIUM,
            RuleSeverity.HIGH, RuleSeverity.CRITICAL,
        ]
        worst = max(matched, key=lambda r: severity_order.index(r.severity))

        is_safe = worst.severity == RuleSeverity.LOW
        classification = "safe" if is_safe else (
            "uncertain" if worst.severity == RuleSeverity.MEDIUM else "malicious"
        )

        logger.warning(
            "Security rules matched in %s: %s",
            context,
            [r.rule_id for r in matched],
        )

        return SecurityResult(
            classification=classification,
            is_safe=is_safe,
            reason=f"Rule {worst.rule_id} triggered: {worst.description}",
            matched_rules=[r.rule_id for r in matched],
            severity=worst.severity.value,
        )

    def _check_task_alignment(
        self,
        action_description: str,
        student_task: str,
        previous_result: SecurityResult,
    ) -> SecurityResult:
        """
        Heuristic: flag actions that introduce operations not mentioned
        in the student's original task (e.g. 'pay' when task is 'check fees').
        """
        RISKY_PATTERNS = [
            (re.compile(r"\bpay\b|\bpayment\b", re.I), "pay", ["pay", "payment"]),
            (re.compile(r"\bdrop\b|\bderegister\b", re.I), "drop elective", ["drop", "deregister", "remove"]),
            (re.compile(r"\bsubmit\s+leave\b", re.I), "submit leave", ["leave", "absent", "application"]),
        ]

        task_lower = student_task.lower()
        for pattern, label, task_keywords in RISKY_PATTERNS:
            if pattern.search(action_description):
                if not any(kw in task_lower for kw in task_keywords):
                    return SecurityResult(
                        classification="uncertain",
                        is_safe=False,
                        reason=(
                            f"Proposed action '{label}' does not appear in the student's "
                            f"original task. Blocking until clarified."
                        ),
                        matched_rules=["ALIGN-001"],
                        severity=RuleSeverity.HIGH.value,
                    )

        return previous_result

