"""
Security service — prompt injection detection.

Architecture:
  1. Deterministic regex rules (utils/security_rules.py) — always applied first.
  2. Structural heuristics — catch obfuscated attacks.
  3. Optional: LLM-assisted classification can be added later but must NEVER
     replace the deterministic layer.

Returns a structured SecurityResult for every piece of untrusted content.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from utils.security_rules import SECURITY_RULES, RuleSeverity, SecurityRule

logger = logging.getLogger(__name__)


@dataclass
class SecurityResult:
    classification: str          # "safe" | "malicious" | "uncertain"
    is_safe: bool
    reason: str
    matched_rules: list[str] = field(default_factory=list)
    severity: Optional[str] = None


class SecurityService:
    """
    Evaluates untrusted content (webpage text, LLM-generated actions)
    for prompt-injection, task-hijacking and credential-extraction attempts.
    """

    # ── Public API ────────────────────────────────────────────────────────────

    def check_webpage_content(
        self, content: str, student_task: str
    ) -> SecurityResult:
        """
        Check raw webpage text for injection attempts.
        `student_task` is kept as the reference ground-truth for the agent's goal.
        """
        return self._run_rules(content, context="webpage")

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
        if any(w in task_lower for w in ["pay", "payment", "fee"]):
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
            (re.compile(r"\bpay\b|\bpayment\b", re.I), "pay", ["check fees", "fee", "pay"]),
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

