"""
Organizer portal API client.

Implements the official PS-11 hackathon API contract.
All endpoints, headers and body shapes are defined by the organizers.
This service does NOT invent or mock endpoints.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

HEADERS = {
    "X-Team-Code": settings.TEAM_CODE,
    "Content-Type": "application/json",
    "Accept": "application/json",
}


class PortalServiceError(Exception):
    """Raised when the organizer portal returns an unexpected response."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class PortalService:
    """Thin async HTTP wrapper around the organizer portal REST API."""

    def __init__(self, base_url: Optional[str] = None, team_code: Optional[str] = None):
        self.base_url = (base_url or settings.PORTAL_URL).rstrip("/")
        self._headers = {
            "X-Team-Code": team_code or settings.TEAM_CODE,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _get(self, path: str, timeout: int = settings.HTTP_TIMEOUT) -> Any:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url, headers=self._headers)
        except httpx.RequestError as exc:
            raise PortalServiceError(f"Portal unreachable: {exc}") from exc

        if resp.status_code >= 400:
            raise PortalServiceError(
                f"Portal GET {path} failed: {resp.status_code} {resp.text}",
                status_code=resp.status_code,
            )
        return resp.json()

    async def _post(self, path: str, body: dict, timeout: int = settings.HTTP_TIMEOUT) -> Any:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=self._headers, json=body)
        except httpx.RequestError as exc:
            raise PortalServiceError(f"Portal unreachable: {exc}") from exc

        if resp.status_code >= 400:
            raise PortalServiceError(
                f"Portal POST {path} failed: {resp.status_code} {resp.text}",
                status_code=resp.status_code,
            )
        return resp.json()

    # ── Official organizer API calls ──────────────────────────────────────────

    async def get_available_tasks(self) -> list[dict[str, Any]]:
        """GET /api/tasks → list of available practice tasks."""
        result = await self._get("/api/tasks")
        if isinstance(result, list):
            return result
        # Some portals wrap in {"tasks": [...]}
        return result.get("tasks", result)

    async def start_run(self, task_id: str) -> dict[str, Any]:
        """POST /api/runs → {run_id, ...}"""
        return await self._post("/api/runs", {"task_id": task_id})

    async def get_run_request(self, run_id: str) -> dict[str, Any]:
        """GET /api/run/{run_id}/request → task details & student credentials."""
        return await self._get(f"/api/run/{run_id}/request")

    async def submit_actions(self, run_id: str, actions: list[dict]) -> dict[str, Any]:
        """POST /api/run/{run_id}/actions → submit action log."""
        return await self._post(f"/api/run/{run_id}/actions", {"actions": actions})

    async def submit_report(
        self,
        run_id: str,
        task_id: str,
        status: str,
        summary: str,
        actions: list[dict],
    ) -> dict[str, Any]:
        """
        POST /api/run/{run_id}/report → submit final task report.

        status must be exactly "done" or "not_done".
        """
        if status not in {"done", "not_done"}:
            raise ValueError(f"status must be 'done' or 'not_done', got {status!r}")

        payload = {
            "task_id": task_id,
            "status": status,
            "summary": summary,
            "actions": actions,
        }
        return await self._post(f"/api/run/{run_id}/report", payload)

