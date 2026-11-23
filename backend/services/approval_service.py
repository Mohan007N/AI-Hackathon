"""
Approval service — official organizer confirmation gate.

Uses the official confirm.py function from the organizer before every
irreversible action. The frontend approval interface is a convenience layer
only; it must NEVER replace the official confirm.py result.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ApprovalServiceError(Exception):
    pass


def _load_confirm_function():
    """
    Dynamically load the official confirm() function from confirm.py
    located in the project root or backend directory.
    Returns None if confirm.py is not found (dev fallback with warning).
    """
    search_paths = [
        Path(__file__).parent.parent / "confirm.py",           # project root
        Path(__file__).parent / "confirm.py",                  # backend/
        Path.cwd() / "confirm.py",
    ]
    for path in search_paths:
        if path.exists():
            spec = importlib.util.spec_from_file_location("confirm", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if hasattr(module, "confirm"):
                logger.info("Loaded official confirm() from %s", path)
                return module.confirm
    logger.warning(
        "confirm.py not found. All irreversible actions will be BLOCKED "
        "until the official confirm.py is placed in the project root."
    )
    return None


_confirm_fn = None


def _get_confirm():
    global _confirm_fn
    if _confirm_fn is None:
        _confirm_fn = _load_confirm_function()
    return _confirm_fn


class ApprovalService:
    """
    Manages official organizer confirmation for irreversible actions.

    Workflow:
    1. Caller describes the exact action.
    2. ApprovalService calls the official confirm(action, base_url, run_id).
    3. Returns True only if confirm() returns True.
    4. Logs every confirmation request and result.
    """

    async def request_confirmation(
        self,
        action_description: str,
        base_url: str,
        run_id: str,
    ) -> dict:
        """
        Request official organizer confirmation for an irreversible action.

        Returns:
            {
                "approved": bool,
                "action_description": str,
                "source": "official_confirm" | "blocked_no_confirm_py",
                "message": str
            }
        """
        confirm = _get_confirm()

        if confirm is None:
            # confirm.py unavailable — BLOCK action for safety
            logger.error(
                "Irreversible action BLOCKED (confirm.py missing): %s",
                action_description,
            )
            return {
                "approved": False,
                "action_description": action_description,
                "source": "blocked_no_confirm_py",
                "message": (
                    "confirm.py not available. Irreversible action blocked. "
                    "Place the official confirm.py in the project root."
                ),
            }

        # Every irreversible action requires a fresh confirmation call
        logger.info(
            "Requesting official confirmation for: '%s' (run_id=%s)",
            action_description,
            run_id,
        )

        try:
            result: bool = confirm(action_description, base_url, run_id)
        except Exception as exc:
            logger.error("confirm() raised an exception: %s", exc, exc_info=True)
            return {
                "approved": False,
                "action_description": action_description,
                "source": "official_confirm",
                "message": f"confirm() raised an exception: {exc}",
            }

        logger.info(
            "Official confirmation result: %s for '%s'",
            result,
            action_description,
        )

        return {
            "approved": bool(result),
            "action_description": action_description,
            "source": "official_confirm",
            "message": "Approved by official confirm.py" if result else "Denied by official confirm.py",
        }

    @staticmethod
    def is_irreversible(action_type: str) -> bool:
        from utils.security_rules import IRREVERSIBLE_ACTIONS
        return action_type in IRREVERSIBLE_ACTIONS

