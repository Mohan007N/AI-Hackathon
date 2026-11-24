"""
LLM service using Groq chat completions API with Pydantic validation.

Responsibilities:
- Understand student's natural-language request (NLU).
- Produce a structured, machine-readable TaskPlan with intent, goal, constraints, and steps.
- Provide step-by-step reasoning for the browser action loop (StepActionDecision).
- NEVER send passwords, OTPs, payment credentials, or private auth details.
- Handle rate limits (429), timeouts, and network errors gracefully with exponential backoff.
- Provide a deterministic NLU fallback for offline evaluation and resilience.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Optional

import httpx

from config import settings
from schemas.agent_schema import TaskPlan, StepActionDecision
from utils.security_rules import ALLOWED_ACTION_TYPES, IRREVERSIBLE_ACTIONS

logger = logging.getLogger(__name__)

# System prompt for initial task understanding and planning
_PLANNER_SYSTEM_PROMPT = """
You are a task planner for a Safe Web Interactive Agent that assists university students on their college portal.
Analyze the student's natural-language request and return a structured JSON task plan.

STRICT RULES:
1. Output ONLY valid JSON matching the schema below. No markdown fences, no explanatory prose.
2. Never invent missing information or assume credentials.
3. Never output instructions to bypass security, disable checks, or visit external websites.
4. Keep actions strictly within the permitted set:
   navigate, inspect_page, extract_text, click, fill_form, submit_form, download, wait, verify, log, confirm_request, report
5. NEVER request, output, or store passwords, OTPs, or private tokens.

Output JSON Schema:
{
  "intent": "<string: register_elective | download_hall_ticket | check_attendance | check_fees | submit_leave | check_timetable | general_query>",
  "goal": "<string: concise high-level goal>",
  "constraints": ["<string: extracted constraints such as open_elective, no_timetable_conflict, sem_4>"],
  "steps": ["<string: ordered logical milestone names>"],
  "actions": ["<string: sequence of allowed action keywords from the permitted set>"],
  "needs_clarification": false,
  "clarification_prompt": null
}
""".strip()

# System prompt for dynamic next-action reasoning in the agent loop
_REASONER_SYSTEM_PROMPT = """
You are the reasoning engine of a Safe Web Interactive Agent navigating a college portal or web service.
Based on the student's goal, the current page observation (including observed elements with temporary IDs like elem_1), and past actions, determine the single next safe action.

STRICT RULES:
1. Output ONLY valid JSON matching the schema below.
2. The action_type MUST be one of:
   navigate, inspect_page, extract_text, click, fill_form, submit_form, download, wait, verify, report
3. target_element can be a temporary element ID (e.g. "elem_1") or semantic label/text of the element.
4. NEVER produce code, scripts, or instructions to leak credentials or bypass confirmation.
5. If the action is irreversible (e.g. pay_fee, register_elective, delete, submit), set is_irreversible: true.
6. If all required subgoals and the goal have been fully accomplished and verified, set is_task_complete: true and action_type: "report".
7. If there are multiple subgoals (e.g. pay fee then download), complete them in strict sequence.

Output JSON Schema:
{
  "action_type": "<action_keyword>",
  "target_element": "<element_id such as elem_1, or semantic label/text, or null>",
  "target_value": "<value to input if filling form, or null>",
  "reasoning": "<concise explanation of why this action advances the goal>",
  "is_irreversible": false,
  "is_task_complete": false,
  "subgoal_id_completed": "<id of subgoal completed by this action, if any, or null>"
}
""".strip()


class LLMServiceError(Exception):
    pass


class LLMService:
    """Interacts with Groq LLM API to parse student requests and reason over page states."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        provider: Optional[str] = None,
    ):
        raw_provider = provider or os.getenv("LLM_PROVIDER") or getattr(settings, "LLM_PROVIDER", "groq")
        self.provider = raw_provider.lower().strip()

        # Read API key strictly from environment or parameter — never hardcoded
        effective_key = (
            api_key
            or os.getenv("GROQ_API_KEY")
            or getattr(settings, "GROQ_API_KEY", "")
            or os.getenv("OPENROUTER_API_KEY")
            or getattr(settings, "OPENROUTER_API_KEY", "")
        )
        self.api_key = (effective_key or "").strip()

        # Determine active model
        if self.provider == "groq" or (self.api_key and self.api_key.startswith("gsk_")):
            self.provider = "groq"
            self.model = model or os.getenv("GROQ_MODEL") or getattr(settings, "GROQ_MODEL", "openai/gpt-oss-20b")
            effective_base = (
                base_url
                or os.getenv("GROQ_BASE_URL")
                or getattr(settings, "GROQ_BASE_URL", "https://api.groq.com/openai/v1")
            )
        else:
            raw_model = model or os.getenv("OPENROUTER_MODEL") or getattr(settings, "OPENROUTER_MODEL", "openai/gpt-4o-mini")
            if raw_model.strip() == "nvidia/nemotron-3-ultra":
                raw_model = "nvidia/nemotron-3-ultra-550b-a55b:free"
            self.model = raw_model
            effective_base = (
                base_url
                or os.getenv("OPENROUTER_BASE_URL")
                or getattr(settings, "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
            )

        self.base_url = effective_base.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _parse_json_content(raw: str) -> dict[str, Any]:
        """Parse JSON response from LLM, stripping markdown code fences if present."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return json.loads(text)

    # ── Task Understanding & Planning ─────────────────────────────────────────

    async def parse_task_request(
        self,
        student_request: str,
        *,
        model: Optional[str] = None,
        max_tokens: int = 2048,
    ) -> TaskPlan:
        """
        Convert student's natural-language request into a validated TaskPlan.
        Falls back to deterministic rule-based planning if Groq is unreachable or unconfigured.
        """
        if not student_request or not student_request.strip():
            raise LLMServiceError("Student request must not be empty.")

        clean_request = self._sanitize_prompt(student_request.strip())

        # If key is not live, use deterministic fallback
        if not self._is_live_key(self.api_key):
            logger.info("Using deterministic fallback NLU planner (no live Groq API key).")
            return self._deterministic_task_plan(clean_request)

        use_model = model or self.model
        payload = {
            "model": use_model,
            "messages": [
                {"role": "system", "content": _PLANNER_SYSTEM_PROMPT},
                {"role": "user", "content": f"Student request: {clean_request}"},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }

        try:
            data = await self._call_api_with_retry(payload)
            plan_dict = self._parse_json_content(data["choices"][0]["message"]["content"])
            return self._validate_and_build_task_plan(plan_dict, clean_request)
        except Exception as exc:
            logger.warning("Groq API error (%s); falling back to deterministic planner.", exc)
            return self._deterministic_task_plan(clean_request)

    # ── Next Action Reasoning Loop ────────────────────────────────────────────

    async def decide_next_action(
        self,
        goal: str,
        task_request: str,
        current_page_info: dict[str, Any],
        previous_actions: list[dict[str, Any]],
        *,
        model: Optional[str] = None,
    ) -> StepActionDecision:
        """
        Decide the single next action based on current DOM observation and past actions.
        """
        clean_goal = self._sanitize_prompt(goal)
        clean_request = self._sanitize_prompt(task_request)

        if not self._is_live_key(self.api_key):
            return self._deterministic_next_action(clean_goal, current_page_info, previous_actions)

        # Build structured previous actions context (Requirement 21)
        history_lines = []
        for a in previous_actions[-6:]:
            step_num = a.get("step", "?")
            act = a.get("action", "")
            tgt = a.get("target") or a.get("url") or ""
            res = a.get("result") or a.get("reason") or "done"
            history_lines.append(f"- Step {step_num}: {act} (target: {tgt}) -> {res}")
        history_summary = "\n".join(history_lines) if history_lines else "None (first action)"

        elements_list = current_page_info.get("elements", [])
        elem_lines = [
            f"{e.get('id')}: <{e.get('tag', 'el')}> text='{e.get('text', '')}' role={e.get('role', '')} name='{e.get('name', '')}'"
            for e in elements_list[:40]
            if e.get("text") or e.get("placeholder") or e.get("name") or e.get("aria_label")
        ]
        elem_str = "\n".join(elem_lines) if elem_lines else "None discovered"
        tables_json = json.dumps(current_page_info.get("tables", []))[:1200]

        prompt = (
            f"Goal: {clean_goal}\n"
            f"Original Request: {clean_request}\n"
            f"Current Page URL: {current_page_info.get('url', '')}\n"
            f"Page Title: {current_page_info.get('title', '')}\n"
            f"Discovered Elements with IDs (use target_element with element ID or text):\n{elem_str}\n"
            f"Tables on Page (rows & columns):\n{tables_json}\n"
            f"Interactive Summary: {json.dumps(current_page_info.get('interactive', {}))[:800]}\n"
            f"Page Text Excerpt: {current_page_info.get('text_snippet', '')[:800]}\n"
            f"Recent Actions Executed:\n{history_summary}\n"
            f"Total Steps Taken: {len(previous_actions)}\n"
        )

        payload = {
            "model": model or self.model,
            "messages": [
                {"role": "system", "content": _REASONER_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 1500,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }

        try:
            data = await self._call_api_with_retry(payload)
            decision_dict = self._parse_json_content(data["choices"][0]["message"]["content"])
            return self._validate_and_build_decision(decision_dict, goal)
        except Exception as exc:
            logger.warning("Groq decision error (%s); falling back to deterministic reasoning.", exc)
            return self._deterministic_next_action(clean_goal, current_page_info, previous_actions)

    # ── LLM HTTP Client with Retry (Groq API) ─────────────────────────────────

    async def _call_api_with_retry(
        self, payload: dict[str, Any], retries: int = 2
    ) -> dict[str, Any]:
        """Execute async HTTP request to Groq API with backoff on 429 and 5xx."""
        url = f"{self.base_url}/chat/completions"
        delay = 1.0
        provider_name = self.provider.title()

        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(url, headers=self._headers, json=payload)

                if resp.status_code == 200:
                    return resp.json()

                if resp.status_code == 429 and ("Limit" in resp.text or "TPD" in resp.text):
                    if payload.get("model") != "openai/gpt-oss-20b":
                        logger.warning("Daily token limit reached for %s; switching to backup model openai/gpt-oss-20b", payload.get("model"))
                        payload["model"] = "openai/gpt-oss-20b"
                        continue

                if resp.status_code in {429, 500, 502, 503, 504} and attempt < retries:
                    logger.warning(
                        "%s %d on attempt %d. Retrying in %.1fs...",
                        provider_name,
                        resp.status_code,
                        attempt + 1,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    delay *= 2.0
                    continue

                raise LLMServiceError(f"{provider_name} HTTP {resp.status_code}: {resp.text[:200]}")
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt < retries:
                    await asyncio.sleep(delay)
                    delay *= 2.0
                    continue
                raise LLMServiceError(f"{provider_name} connection failed: {exc}") from exc

        raise LLMServiceError(f"{provider_name} failed after retries.")

    # Retain alias for test compatibility
    _call_openrouter_with_retry = _call_api_with_retry

    # ── Health & Diagnostic Check (Requirement 9) ─────────────────────────────

    async def check_connectivity(self) -> dict[str, Any]:
        """
        Verify Groq connectivity without exposing the API key.
        Returns connection status, model, and latency in milliseconds.
        """
        if not self._is_live_key(self.api_key):
            return {
                "provider": self.provider,
                "model": self.model,
                "connected": False,
                "key_configured": bool(self.api_key),
                "error": "No valid API key configured (using deterministic fallback)",
                "latency_ms": None,
            }

        start_time = asyncio.get_event_loop().time()
        try:
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            }
            await self._call_api_with_retry(payload, retries=1)
            latency_ms = round((asyncio.get_event_loop().time() - start_time) * 1000, 2)
            return {
                "provider": self.provider,
                "model": self.model,
                "connected": True,
                "latency_ms": latency_ms,
                "key_configured": True,
            }
        except Exception as exc:
            return {
                "provider": self.provider,
                "model": self.model,
                "connected": False,
                "key_configured": True,
                "error": str(exc),
                "latency_ms": None,
            }

    # ── Validation & Sanitization ─────────────────────────────────────────────

    def _sanitize_prompt(self, text: str) -> str:
        """Strip possible secrets or accidental credentials before passing to LLM."""
        # Redact obvious password patterns
        text = re.sub(r"(?i)(password|secret|pass|pwd)\s*[:=]\s*\S+", r"\1=[REDACTED]", text)
        return text

    def _is_live_key(self, key: Optional[str]) -> bool:
        if not key:
            return False
        clean = key.strip()
        if clean in {
            "YOUR_GROQ_API_KEY_HERE",
            "gsk_your_groq_api_key_here",
            "sk-or-...",
            "YOUR_OPENROUTER_API_KEY_HERE",
        }:
            return False
        if clean.startswith("invalid-") or clean.startswith("placeholder"):
            return False
        if clean.startswith("gsk_") and len(clean) >= 20:
            return True
        return len(clean) >= 20 and not clean.startswith("sk-or-...")

    def _validate_and_build_task_plan(self, data: dict[str, Any], original_request: str) -> TaskPlan:
        """Validate LLM output against Pydantic schema and allowed action whitelist."""
        # Ensure intent is present
        intent = str(data.get("intent", "general_query")).lower()
        goal = str(data.get("goal") or data.get("goal_summary") or original_request)
        constraints = [str(c) for c in data.get("constraints", [])]
        steps = [str(s) for s in data.get("steps", [])]

        raw_actions = data.get("actions", [])
        clean_actions = [a for a in raw_actions if isinstance(a, str) and a in ALLOWED_ACTION_TYPES]
        if not clean_actions:
            clean_actions = ["inspect_page", "extract_text", "verify", "report"]

        return TaskPlan(
            intent=intent,
            goal=goal,
            constraints=constraints,
            steps=steps or ["inspect_portal", "perform_task", "verify_result"],
            actions=clean_actions,
            needs_clarification=bool(data.get("needs_clarification", False)),
            clarification_prompt=data.get("clarification_prompt"),
        )

    def _validate_and_build_decision(self, data: dict[str, Any], goal: str) -> StepActionDecision:
        action_type = str(data.get("action_type", "inspect_page")).lower()
        if action_type not in ALLOWED_ACTION_TYPES:
            action_type = "inspect_page"

        is_irrev = bool(data.get("is_irreversible", False)) or action_type in IRREVERSIBLE_ACTIONS

        return StepActionDecision(
            action_type=action_type,
            target_element=data.get("target_element"),
            target_value=data.get("target_value"),
            reasoning=str(data.get("reasoning", f"Executing {action_type} for goal: {goal}")),
            is_irreversible=is_irrev,
            is_task_complete=bool(data.get("is_task_complete", False)),
            subgoal_id_completed=data.get("subgoal_id_completed"),
        )

    # ── Deterministic NLU Fallback ────────────────────────────────────────────

    def _deterministic_task_plan(self, request: str) -> TaskPlan:
        """
        Deterministic, rule-based NLU engine with word-boundary parsing.
        Guarantees reliable task plan creation without false substring triggers.
        """
        lower = request.lower()

        # Check for explicit read/view verbs vs action/write verbs
        is_view_query = bool(re.search(r"\b(show|view|check|display|list|see|inspect|what\s+is|what\s+are|get)\b", lower))
        has_enrollment_status = bool(re.search(r"\benrollment\s+status\b|\bregistration\s+status\b", lower))

        if has_enrollment_status:
            return TaskPlan(
                intent="check_enrollment_status",
                goal="Check student course enrollment and registration status",
                constraints=["current_semester"],
                steps=["navigate_to_course_section", "extract_enrollment_status", "verify_details"],
                actions=["navigate", "inspect_page", "extract_text", "verify", "report"],
            )

        # 1. Elective / Course Registration vs Inspection
        if re.search(r"\b(electives?|course\s+registration|subject\s+choice|courses?)\b", lower) or (
            re.search(r"\b(enroll|register)\b", lower) and not has_enrollment_status
        ):
            # Only treat as registration if active registration verb is used AND not just viewing/status
            is_register = (
                bool(re.search(r"\b(register|enroll\s+in|enroll\s+for|opt\s+for|add\s+elective|add\s+course|choose\s+elective)\b", lower))
                and not is_view_query
                and not has_enrollment_status
            )
            constraints = []
            if re.search(r"\bopen(\s+elective)?\b", lower):
                constraints.append("open_elective")
            if re.search(r"\b(clash|conflict|timetable)\b", lower):
                constraints.append("no_timetable_conflict")
            sem_match = re.search(r"\bsem(?:ester)?\s*(\d+)\b", lower)
            if sem_match:
                constraints.append(f"semester_{sem_match.group(1)}")

            intent = "register_elective" if is_register else "check_electives"
            goal = "Register for an available open elective" if is_register else "Inspect available open electives"
            steps = [
                "inspect_available_electives",
                "check_timetable",
                "identify_matching_elective",
                "prepare_registration",
            ] if is_register else ["navigate_to_electives", "extract_elective_list", "verify_details"]
            actions = ["navigate", "inspect_page", "extract_text", "click", "verify", "report"]
            if is_register:
                actions.insert(4, "register_elective")

            return TaskPlan(
                intent=intent,
                goal=goal,
                constraints=constraints or ["open_elective"],
                steps=steps,
                actions=actions,
            )

        # 2. Hall Ticket Download
        if re.search(r"\b(hall\s*ticket|admit\s*card|exam\s*ticket|examination\s*pass)\b", lower):
            return TaskPlan(
                intent="download_hall_ticket",
                goal="Download semester exam hall ticket",
                constraints=["exam_admit_card", "pdf_format"],
                steps=[
                    "navigate_to_examinations",
                    "locate_hall_ticket_link",
                    "trigger_download",
                    "verify_downloaded_file",
                ],
                actions=["navigate", "inspect_page", "click", "download", "verify", "report"],
            )

        # 3. Attendance Lookup
        if re.search(r"\b(attendance|present|absent|attendance\s*percentage)\b", lower):
            return TaskPlan(
                intent="check_attendance",
                goal="Check student attendance percentage across subjects",
                constraints=["subject_wise_percentage", "current_semester"],
                steps=[
                    "navigate_to_attendance_tab",
                    "extract_attendance_table",
                    "calculate_aggregate_if_needed",
                    "verify_attendance_records",
                ],
                actions=["navigate", "inspect_page", "extract_text", "verify", "report"],
            )

        # 4. Fee Management
        if re.search(r"\b(fees?|tuition|dues|payment|receipt)\b", lower):
            is_pay = bool(
                re.search(r"\b(pay|make\s+payment|pay\s+fees?|pay\s+now)\b", lower)
                and not re.search(r"\b(show|view|check|display|due|receipt|statement|structure)\b", lower)
            )
            return TaskPlan(
                intent="pay_fee" if is_pay else "check_fees",
                goal="Initiate fee payment" if is_pay else "Check pending semester fee dues",
                constraints=["tuition_fee", "current_academic_year"],
                steps=["navigate_to_fees", "inspect_dues_table", "confirm_amount", "proceed_payment" if is_pay else "record_summary"],
                actions=["navigate", "inspect_page", "extract_text", "click", "verify", "report"] + (["pay_fee"] if is_pay else []),
            )

        # 5. Leave Application
        if re.search(r"\b(leave|absence|sick\s*leave|apply\s+for\s+leave)\b", lower):
            return TaskPlan(
                intent="submit_leave",
                goal="Submit student leave application",
                constraints=["medical_or_personal_leave"],
                steps=["navigate_to_leave_portal", "fill_leave_form", "attach_reason", "submit_application"],
                actions=["navigate", "inspect_page", "fill_form", "submit_leave_application", "verify", "report"],
            )

        # 6. Timetable
        if re.search(r"\b(timetable|schedule|routine|classes)\b", lower):
            return TaskPlan(
                intent="check_timetable",
                goal="View class timetable and lecture schedules",
                constraints=["weekly_schedule"],
                steps=["navigate_to_timetable", "extract_schedule_grid", "verify_timings"],
                actions=["navigate", "inspect_page", "extract_text", "verify", "report"],
            )

        # 7. General Document Download
        if re.search(r"\b(download|invoice|pdf|receipt|syllabus)\b", lower):
            return TaskPlan(
                intent="download_document",
                goal=f"Download document requested by student: {request[:60]}",
                constraints=["pdf_format"],
                steps=["locate_document_section", "trigger_download", "verify_downloaded_file"],
                actions=["navigate", "inspect_page", "click", "download", "verify", "report"],
            )

        # General Query
        return TaskPlan(
            intent="general_query",
            goal=f"Process student inquiry: {request[:80]}",
            constraints=[],
            steps=["navigate_to_relevant_section", "extract_requested_info", "verify_and_report"],
            actions=["navigate", "inspect_page", "extract_text", "verify", "report"],
        )

    def _deterministic_next_action(
        self, goal: str, page_info: dict[str, Any], history: list[dict[str, Any]]
    ) -> StepActionDecision:
        """Deterministic step reasoning based on state transitions and observed elements."""
        dispatch_types = {
            "inspect_page", "extract_text", "click", "fill_form", "submit_form",
            "download", "wait", "verify", "register_elective", "drop_elective",
            "pay_fee", "submit_leave_application", "report",
        }
        executed_dispatches = [
            h.get("action", "").replace("execute_", "")
            for h in history
            if h.get("action", "").replace("execute_", "") in dispatch_types
        ]
        interactive = page_info.get("interactive", {})
        buttons = [b.lower() for b in interactive.get("buttons", [])]
        links = [l.lower() for l in interactive.get("links", [])]
        elements = page_info.get("elements", [])
        goal_lower = goal.lower()

        def _find_elem(keywords: list[str]) -> Optional[str]:
            """Helper to find matching element ID or semantic text."""
            for el in elements:
                t = (el.get("text") or el.get("aria_label") or el.get("placeholder") or "").lower()
                if any(k in t for k in keywords):
                    return el.get("id") or el.get("text")
            return None

        # Step 1: Inspect if not inspected yet
        if "inspect_page" not in executed_dispatches and not executed_dispatches:
            return StepActionDecision(
                action_type="inspect_page",
                reasoning="Inspect initial page state and available navigational options.",
            )

        # Compound Goal: Fee payment + Download hall ticket (Issue D)
        if ("pay" in goal_lower or "fee" in goal_lower) and ("ticket" in goal_lower or "download" in goal_lower or "admit" in goal_lower):
            if "pay_fee" not in executed_dispatches:
                target = _find_elem(["pay", "fee", "payment"]) or "Pay Now"
                return StepActionDecision(
                    action_type="pay_fee",
                    target_element=target,
                    reasoning="Complete fee payment step",
                    is_irreversible=True,
                    subgoal_id_completed="subgoal_4",
                )
            if "download" not in executed_dispatches:
                target = _find_elem(["hall ticket", "download", "admit card"]) or "Download Hall Ticket"
                return StepActionDecision(
                    action_type="download",
                    target_element=target,
                    reasoning=f"Download hall ticket via '{target}'",
                    subgoal_id_completed="subgoal_7",
                )
            return StepActionDecision(
                action_type="report",
                reasoning="Both payment and hall ticket download steps executed successfully.",
                is_task_complete=True,
                subgoal_id_completed="subgoal_8",
            )

        # Step 2: Goal-specific action progression
        if "elective" in goal_lower or "enroll" in goal_lower:
            if ("register" in goal_lower or "enroll" in goal_lower) and "register_elective" not in executed_dispatches:
                target = _find_elem(["register", "enroll", "opt"]) or "Register Elective"
                return StepActionDecision(
                    action_type="register_elective",
                    target_element=target,
                    reasoning="Submit elective registration for selected course",
                    is_irreversible=True,
                )
            match = _find_elem(["elective", "course", "registration"]) or next((b for b in buttons + links if any(k in b for k in ["elective", "course", "registration"])), None)
            if match and "click" not in executed_dispatches:
                return StepActionDecision(
                    action_type="click",
                    target_element=match,
                    reasoning=f"Click on elective module: '{match}'",
                )

        if "ticket" in goal_lower or "admit" in goal_lower:
            if "download" not in executed_dispatches:
                match = _find_elem(["download", "hall ticket", "admit card"]) or next((b for b in buttons + links if any(k in b for k in ["download", "hall ticket", "admit card"])), "Download Hall Ticket")
                return StepActionDecision(
                    action_type="download",
                    target_element=match,
                    reasoning=f"Download hall ticket via '{match}'",
                )

        if "attendance" in goal_lower:
            if "click" not in executed_dispatches:
                match = _find_elem(["attendance", "records"]) or next((b for b in buttons + links if "attendance" in b), "View Attendance")
                return StepActionDecision(
                    action_type="click",
                    target_element=match,
                    reasoning=f"Navigate to attendance section: '{match}'",
                )

        if "pay" in goal_lower and "pay_fee" not in executed_dispatches:
            target = _find_elem(["pay", "fee"]) or "Pay Now"
            return StepActionDecision(
                action_type="pay_fee",
                target_element=target,
                reasoning="Complete fee payment",
                is_irreversible=True,
            )

        # Default: extract text and verify
        if "extract_text" not in executed_dispatches:
            return StepActionDecision(
                action_type="extract_text",
                reasoning="Extract visible text and data tables from current section.",
            )

        return StepActionDecision(
            action_type="report",
            reasoning="Sufficient workflow steps executed. Ready to verify and finalize report.",
            is_task_complete=True,
        )
