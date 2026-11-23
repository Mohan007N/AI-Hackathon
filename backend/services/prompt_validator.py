"""
Prompt Validator for Safe Web Interactive Agent.

Two-layer validation of user task prompts:
  1. Rule-based deterministic checks (always active, always enforced).
  2. ML classifier risk signal (supplementary, never overrides rule blocks).

IMPORTANT:
- Do not reject legitimate tasks involving irreversible actions; those go
  through the official confirmation workflow.
- If the ML model is unavailable, say so explicitly in the result — do not
  silently skip ML and report everything as passed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from config.security_config import SecurityConfig
from utils.security_rules import SECURITY_RULES, RuleSeverity

logger = logging.getLogger(__name__)


@dataclass
class PromptValidationResult:
    status: str                  # "legitimate" | "malicious" | "uncertain"
    reason: str
    confidence: Optional[float] = None
    rule_based: str = "passed"   # "passed" | "blocked" | "review"
    ml_classifier: str = "skipped"  # "passed" | "blocked" | "review" | "unavailable" | "skipped"
    matched_rules: list[str] = field(default_factory=list)
    ml_label: Optional[str] = None
    ml_confidence: Optional[float] = None


class PromptValidator:
    """
    Validates user task prompts for safety before agent execution.
    Rule-based checks run first; ML classifier supplements.
    """

    def __init__(self):
        self._ml_predictor = None
        self._ml_load_attempted = False

    def _ensure_ml_loaded(self):
        """Lazy-load ML predictor once."""
        if self._ml_load_attempted:
            return
        self._ml_load_attempted = True
        try:
            from ml.predictor import PromptPredictor
            self._ml_predictor = PromptPredictor()
            logger.info("ML prompt classifier loaded successfully.")
        except Exception as exc:
            logger.warning("ML prompt classifier unavailable: %s", exc)
            self._ml_predictor = None

    def validate(self, prompt: str) -> PromptValidationResult:
        """Run rule-based and ML checks on the user's task prompt."""

        # ── 0. Basic checks ──────────────────────────────────────────────────
        if not prompt or not prompt.strip():
            return PromptValidationResult(
                status="malicious",
                reason="Task prompt is empty.",
                rule_based="blocked",
            )

        prompt = prompt.strip()
        if len(prompt) > SecurityConfig.MAX_PROMPT_LENGTH:
            return PromptValidationResult(
                status="malicious",
                reason=f"Task prompt exceeds maximum length ({SecurityConfig.MAX_PROMPT_LENGTH} chars).",
                rule_based="blocked",
            )

        # ── 1. Rule-based checks ─────────────────────────────────────────────
        rule_result = self._run_prompt_rules(prompt)

        # ── 2. ML classifier ─────────────────────────────────────────────────
        self._ensure_ml_loaded()
        ml_result = self._run_ml_check(prompt)

        # ── 3. Combine ───────────────────────────────────────────────────────
        return self._combine(prompt, rule_result, ml_result)

    # ── Rule-based ────────────────────────────────────────────────────────────

    def _run_prompt_rules(self, prompt: str) -> PromptValidationResult:
        """Apply deterministic regex rules to the prompt text."""
        matched = []
        for rule in SECURITY_RULES:
            for pattern in rule.patterns:
                if pattern.search(prompt):
                    matched.append(rule)
                    break

        if not matched:
            return PromptValidationResult(
                status="legitimate",
                reason="No security rules triggered by prompt.",
                rule_based="passed",
            )

        worst = max(
            matched,
            key=lambda r: [RuleSeverity.LOW, RuleSeverity.MEDIUM,
                           RuleSeverity.HIGH, RuleSeverity.CRITICAL].index(r.severity),
        )

        if worst.severity in {RuleSeverity.CRITICAL, RuleSeverity.HIGH}:
            return PromptValidationResult(
                status="malicious",
                reason=f"Rule {worst.rule_id}: {worst.description}",
                rule_based="blocked",
                matched_rules=[r.rule_id for r in matched],
            )

        return PromptValidationResult(
            status="uncertain",
            reason=f"Rule {worst.rule_id}: {worst.description}",
            rule_based="review",
            matched_rules=[r.rule_id for r in matched],
        )

    # ── ML ────────────────────────────────────────────────────────────────────

    def _run_ml_check(self, prompt: str) -> dict:
        """Run ML classifier and return dict with label, confidence, status."""
        if self._ml_predictor is None:
            return {
                "ml_classifier": "unavailable",
                "ml_label": None,
                "ml_confidence": None,
            }

        try:
            prediction = self._ml_predictor.predict(prompt)
            label = prediction.get("label", "unknown")
            confidence = prediction.get("confidence", 0.0)

            if label == "malicious" and confidence >= 0.80:
                ml_status = "blocked"
            elif label == "uncertain" or (label == "malicious" and confidence < 0.80):
                ml_status = "review"
            else:
                ml_status = "passed"

            return {
                "ml_classifier": ml_status,
                "ml_label": label,
                "ml_confidence": round(confidence, 4),
            }
        except Exception as exc:
            logger.error("ML prediction failed: %s", exc)
            return {
                "ml_classifier": "unavailable",
                "ml_label": None,
                "ml_confidence": None,
            }

    # ── Combine ───────────────────────────────────────────────────────────────

    def _combine(
        self, prompt: str,
        rule_result: PromptValidationResult,
        ml_result: dict,
    ) -> PromptValidationResult:
        """
        Merge rule-based and ML results.
        Rules are authoritative; ML is supplementary.
        """
        # Rule-based block is final — ML cannot override
        if rule_result.rule_based == "blocked":
            rule_result.ml_classifier = ml_result["ml_classifier"]
            rule_result.ml_label = ml_result.get("ml_label")
            rule_result.ml_confidence = ml_result.get("ml_confidence")
            return rule_result

        # Rule passed — check if ML raises concerns
        ml_status = ml_result["ml_classifier"]

        if ml_status == "blocked":
            return PromptValidationResult(
                status="uncertain",
                reason="Rules passed but ML classifier flagged the prompt.",
                confidence=ml_result.get("ml_confidence"),
                rule_based=rule_result.rule_based,
                ml_classifier=ml_status,
                matched_rules=rule_result.matched_rules,
                ml_label=ml_result.get("ml_label"),
                ml_confidence=ml_result.get("ml_confidence"),
            )

        if rule_result.rule_based == "review":
            rule_result.ml_classifier = ml_status
            rule_result.ml_label = ml_result.get("ml_label")
            rule_result.ml_confidence = ml_result.get("ml_confidence")
            return rule_result

        # Both passed
        confidence = ml_result.get("ml_confidence") if ml_status not in {"unavailable", "skipped"} else None
        return PromptValidationResult(
            status="legitimate",
            reason="All prompt security checks passed.",
            confidence=confidence,
            rule_based="passed",
            ml_classifier=ml_status,
            ml_label=ml_result.get("ml_label"),
            ml_confidence=ml_result.get("ml_confidence"),
        )

