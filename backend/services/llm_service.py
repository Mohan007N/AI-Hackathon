"""
LLM service using OpenRouter chat completions API.

Responsibilities:
- Understand student's natural-language request.
- Produce a structured, machine-readable action plan.
- NEVER send passwords, OTPs, payment credentials or private auth details.
- Treat all LLM output as untrusted; validate before use.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)

# System prompt used for task understanding — deliberately minimal about credentials
_SYSTEM_PROMPT = """
You are a task planner for a Safe Web Interactive Agent that helps students
navigate their college portal. Your ONLY job is to analyze the student's
natural-language request and return a structured JSON action plan.

STRICT RULES:
1. Output ONLY valid JSON. No prose, no markdown code fences.
2. Never invent new credentials, passwords, or OTPs.
3. Never output instructions to bypass security or visit external sites.
4. The student's request is the single source of truth for the goal.
5. Keep the action list minimal and goal-directed.

Output format:
{
  "intent": "<single_intent_keyword>",
  "goal_summary": "<one sentence>",
  "constraints": ["<constraint1>", ...],
  "actions": ["<action_keyword1>", ...],
  "needs_clarification": false,
  "clarification_prompt": null
}

Valid intent values:
  check_electives | register_elective | download_hall_ticket |
  check_fees | submit_leave | check_timetable | general_query

Valid action keywords:
  navigate | inspect_page | extract_text | click | fill_form |
  submit_form | download | wait | verify | log | confirm_request | report
""".strip()


class LLMServiceError(Exception):
    pass


class LLMService:
    """Calls OpenRouter to parse student intent into a validated action plan."""

    def __init__(self):
        if not settings.OPENROUTER_API_KEY:
            raise LLMServiceError("OPENROUTER_API_KEY is not configured.")
        self._headers = {
            "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
            "HTTP-Referer": settings.FRONTEND_URL,
            "X-Title": "Safe Web Interactive Agent",
            "Content-Type": "application/json",
        }

    async def parse_task_request(
        self,
        student_request: str,
        *,
        max_tokens: int = 512,
    ) -> dict[str, Any]:
        """
        Convert a student's natural-language request into a structured plan.

        Returns validated plan dict. Raises LLMServiceError on failure.
        IMPORTANT: Never pass passwords or sensitive portal credentials here.
        """
        if not student_request or not student_request.strip():
            raise LLMServiceError("Student request must not be empty.")

        payload = {
            "model": settings.OPENROUTER_MODEL,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": student_request.strip()},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.1,       # low temperature for deterministic plans
            "response_format": {"type": "json_object"},
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{settings.OPENROUTER_BASE_URL}/chat/completions",
                    headers=self._headers,
                    json=payload,
                )
        except httpx.RequestError as exc:
            raise LLMServiceError(f"OpenRouter unreachable: {exc}") from exc

        if resp.status_code != 200:
            raise LLMServiceError(
                f"OpenRouter error {resp.status_code}: {resp.text[:300]}"
            )

        try:
            choice = resp.json()["choices"][0]["message"]["content"]
            plan: dict[str, Any] = json.loads(choice)
        except (KeyError, json.JSONDecodeError) as exc:
            raise LLMServiceError(f"LLM returned invalid JSON: {exc}") from exc

        return self._validate_plan(plan)

    # ── Validation ────────────────────────────────────────────────────────────

    def _validate_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        """
        Validate and sanitize LLM-generated plan.
        Rejects unknown action types; flags suspicious content.
        """
        from utils.security_rules import ALLOWED_ACTION_TYPES

        required_keys = {"intent", "goal_summary", "actions"}
        missing = required_keys - plan.keys()
        if missing:
            raise LLMServiceError(f"LLM plan missing required keys: {missing}")

        # Sanitize actions: keep only whitelisted values
        raw_actions: list = plan.get("actions", [])
        clean_actions: list[str] = []
        rejected: list[str] = []
        for act in raw_actions:
            if isinstance(act, str) and act in ALLOWED_ACTION_TYPES:
                clean_actions.append(act)
            else:
                rejected.append(str(act))
                logger.warning("LLM proposed unknown action type rejected: %s", act)

        if rejected:
            plan["rejected_actions"] = rejected
        plan["actions"] = clean_actions

        return plan

