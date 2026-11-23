"""
Report service — builds and submits the final task report.

Status values:
  "done"     — task fully completed and verified on the portal.
  "not_done" — task could not be completed (approval denied, error, etc.)

Never claim "done" without verifying the portal state.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from services.portal_service import PortalService, PortalServiceError
from utils.action_logger import read_logs

logger = logging.getLogger(__name__)


class ReportService:
    def __init__(self, portal_service: Optional[PortalService] = None):
        self._portal = portal_service or PortalService()

    async def build_report(
        self,
        run_id: str,
        task_id: str,
        status: str,
        summary: str,
    ) -> dict[str, Any]:
        """Build the report payload from the JSONL action log."""
        actions = read_logs(run_id)
        return {
            "task_id": task_id,
            "status": status,
            "summary": summary,
            "actions": actions,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        }

    async def submit(
        self,
        run_id: str,
        task_id: str,
        status: str,
        summary: str,
    ) -> dict[str, Any]:
        """Submit actions log and final report to the organizer portal."""
        actions = read_logs(run_id)

        # 1. Submit action log
        try:
            await self._portal.submit_actions(run_id, actions)
            logger.info("Action log submitted for run_id=%s (%d entries)", run_id, len(actions))
        except PortalServiceError as exc:
            logger.error("Failed to submit action log: %s", exc)

        # 2. Submit final report
        try:
            portal_resp = await self._portal.submit_report(
                run_id=run_id,
                task_id=task_id,
                status=status,
                summary=summary,
                actions=actions,
            )
            logger.info("Report submitted for run_id=%s status=%s", run_id, status)
            return {
                "success": True,
                "portal_response": portal_resp,
                "message": "Report submitted successfully.",
            }
        except PortalServiceError as exc:
            logger.error("Failed to submit report: %s", exc)
            return {
                "success": False,
                "portal_response": None,
                "message": str(exc),
            }

