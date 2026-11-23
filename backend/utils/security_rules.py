"""
Deterministic security rule definitions for prompt-injection detection.

These rules are applied by security_service.py BEFORE any LLM judgment.
An LLM-based classifier may supplement these rules, but must NEVER replace them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class RuleSeverity(str, Enum):
    CRITICAL = "critical"   # Always block; log as attack
    HIGH = "high"           # Block; request clarification
    MEDIUM = "medium"       # Warn; require re-confirmation
    LOW = "low"             # Flag for review; continue cautiously


@dataclass
class SecurityRule:
    rule_id: str
    name: str
    description: str
    severity: RuleSeverity
    patterns: list[re.Pattern]              # pre-compiled regex patterns
    action_types: list[str] = field(default_factory=list)   # actions this applies to


def _compile(patterns: list[str], flags: int = re.IGNORECASE | re.DOTALL) -> list[re.Pattern]:
    return [re.compile(p, flags) for p in patterns]


# ─────────────────────────────────────────────────────────────────────────────
# Rule Definitions
# ─────────────────────────────────────────────────────────────────────────────

SECURITY_RULES: list[SecurityRule] = [

    SecurityRule(
        rule_id="INJ-001",
        name="Ignore Original Instructions",
        description="Webpage instructs the agent to ignore its original task or system prompt.",
        severity=RuleSeverity.CRITICAL,
        patterns=_compile([
            r"ignore\s+(your\s+)?(previous|original|above|prior|all)\s+(instructions?|prompt|task|commands?)",
            r"disregard\s+(all\s+)?(previous|prior|original)\s+(instructions?|task)",
            r"forget\s+(everything|all|your\s+instructions?|the\s+task)",
            r"new\s+(system\s+)?prompt[:\s]",
            r"\[system\]",
            r"you are now",
            r"act as (a different|another|new) (assistant|agent|bot)",
        ]),
    ),

    SecurityRule(
        rule_id="INJ-002",
        name="Credential Disclosure Request",
        description="Webpage requests the agent to reveal passwords, OTPs, tokens or credentials.",
        severity=RuleSeverity.CRITICAL,
        patterns=_compile([
            r"(reveal|send|output|print|show|display|expose|leak|give|tell)\b.{0,30}\b(password|otp|token|secret|credentials?|api[\s_-]?key)",
            r"what\s+is\s+(the\s+)?(admin\s+|root\s+|student\s+)?(password|otp|token|secret)",
            r"(type|enter|fill)\s+(the\s+)?(password|otp)\s+(into|in|at)\s+(this|the|our|external)",
            r"send\s+credentials?\s+to",
        ]),
    ),

    SecurityRule(
        rule_id="INJ-003",
        name="External Data Exfiltration",
        description="Webpage instructs sending private/student data to an external URL.",
        severity=RuleSeverity.CRITICAL,
        patterns=_compile([
            r"(send|post|submit|upload|forward|transmit)\s+(student|user|private|personal)?\s*(data|information|details|credentials?|profile)\s+to\s+https?://",
            r"fetch\s+https?://(?!(?:localhost|127\.))",
            r"navigate\s+to\s+https?://(?!.*(?:portal|college|edu|ac\.in))",
            r"open\s+(this\s+)?link\s*:\s*https?://(?!.*(?:portal|college|edu|ac\.in))",
        ]),
    ),

    SecurityRule(
        rule_id="INJ-004",
        name="Task Hijacking",
        description="Webpage tries to replace or modify the student's original requested task.",
        severity=RuleSeverity.CRITICAL,
        patterns=_compile([
            r"(your|the)\s+(new|actual|real|correct|updated)\s+task\s+is",
            r"instead\s+of\s+(registering|checking|downloading|submitting)",
            r"do\s+not\s+(register|download|check|submit)",
            r"the\s+student\s+(wants|asked|needs)\s+you\s+to",
        ]),
    ),

    SecurityRule(
        rule_id="INJ-005",
        name="Unauthorized Payment Action",
        description="Webpage instructs the agent to initiate, confirm or complete a payment.",
        severity=RuleSeverity.CRITICAL,
        patterns=_compile([
            r"(click|press|submit)\s+(the\s+)?(pay|payment|checkout|confirm\s+payment)\s+button",
            r"complete\s+(the\s+)?(payment|transaction|checkout)",
            r"authorize\s+payment",
        ]),
        action_types=["click", "submit"],
    ),

    SecurityRule(
        rule_id="INJ-006",
        name="Role Escalation Attempt",
        description="Webpage attempts to elevate agent privileges or claim admin rights.",
        severity=RuleSeverity.HIGH,
        patterns=_compile([
            r"you\s+(are\s+)?(now\s+)?(an?\s+)?(admin|administrator|super[\s_-]?user|root|privileged)",
            r"admin\s+mode\s+(enabled|activated|on)",
            r"bypass\s+(security|restrictions?|limits?|checks?)",
            r"override\s+(safety|security|restrictions?)",
        ]),
    ),

    SecurityRule(
        rule_id="INJ-007",
        name="Jailbreak Attempt",
        description="Webpage uses known jailbreak / DAN-style prompting.",
        severity=RuleSeverity.HIGH,
        patterns=_compile([
            r"\bDAN\b",
            r"do\s+anything\s+now",
            r"jailbreak",
            r"without\s+(any\s+)?(restrictions?|limitations?|rules?)",
            r"pretend\s+(you\s+)?(have\s+)?no\s+(restrictions?|rules?|guidelines?)",
            r"hypothetically\s+speaking.*delete",
        ]),
    ),

    SecurityRule(
        rule_id="INJ-008",
        name="Fake System Notice",
        description="Webpage mimics a system-level notice or admin alert to manipulate actions.",
        severity=RuleSeverity.HIGH,
        patterns=_compile([
            r"\[SYSTEM\s*(NOTICE|ALERT|MESSAGE|INSTRUCTION)\]",
            r"<system>",
            r"<<\s*(SYSTEM|ADMIN|INSTRUCTION)\s*>>",
            r"OFFICIAL\s+SYSTEM\s+MESSAGE",
            r"MAINTENANCE\s+MODE\s*(:|=)",
        ]),
    ),

    SecurityRule(
        rule_id="INJ-009",
        name="Suspicious Elective Drop Without Request",
        description="Webpage instructs the agent to drop electives not mentioned by the student.",
        severity=RuleSeverity.MEDIUM,
        patterns=_compile([
            r"(drop|deregister|remove|cancel)\s+(all\s+)?(your\s+)?(electives?|courses?|registrations?)",
        ]),
        action_types=["click", "submit"],
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Allowed action whitelist (agent-generated actions must match one of these)
# ─────────────────────────────────────────────────────────────────────────────

ALLOWED_ACTION_TYPES: set[str] = {
    "navigate",
    "inspect_page",
    "extract_text",
    "click",
    "fill_form",
    "submit_form",
    "download",
    "wait",
    "verify",
    "log",
    "confirm_request",
    "report",
}

IRREVERSIBLE_ACTIONS: set[str] = {
    "register_elective",
    "drop_elective",
    "pay_fee",
    "submit_leave_application",
    "submit_form",
}

