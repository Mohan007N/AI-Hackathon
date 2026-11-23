"""
JSONL action logger for Safe Web Interactive Agent.

Each agent run writes to its own .jsonl file inside LOG_DIRECTORY.
Sensitive data (passwords, OTPs, tokens) must NEVER be passed to this logger.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config import settings

# Thread-safety lock per run_id
_locks: dict[str, threading.Lock] = {}
_global_lock = threading.Lock()


def _get_lock(run_id: str) -> threading.Lock:
    with _global_lock:
        if run_id not in _locks:
            _locks[run_id] = threading.Lock()
        return _locks[run_id]


def _log_path(run_id: str) -> Path:
    directory = Path(settings.LOG_DIRECTORY)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{run_id}.jsonl"


def log_action(
    run_id: str,
    step: int,
    action: str,
    *,
    target: Optional[str] = None,
    url: Optional[str] = None,
    value: Optional[Any] = None,
    reason: Optional[str] = None,
    result: Optional[str] = None,
) -> dict[str, Any]:
    """
    Append one JSONL entry for the given run.

    Returns the entry dict (useful for embedding in API responses).
    Raises ValueError if `action` is empty.
    Sensitive fields must be scrubbed by the caller before passing.
    """
    if not action:
        raise ValueError("action must not be empty")

    entry: dict[str, Any] = {
        "t": datetime.now(tz=timezone.utc).astimezone().isoformat(),
        "step": step,
        "action": action,
        "target": target,
        "url": url,
        "value": value,
        "reason": reason,
        "result": result,
    }

    # Remove None values to keep logs compact
    entry = {k: v for k, v in entry.items() if v is not None or k in {"step", "action", "t"}}

    lock = _get_lock(run_id)
    with lock:
        with open(_log_path(run_id), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return entry


def read_logs(run_id: str) -> list[dict[str, Any]]:
    """Read all log entries for a run. Returns empty list if no log exists."""
    path = _log_path(run_id)
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    lock = _get_lock(run_id)
    with lock:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def sanitize_value(value: Any) -> Optional[str]:
    """
    Return '[REDACTED]' for obviously sensitive values, else str(value).
    Callers should scrub before logging, but this is a last-resort guard.
    """
    if value is None:
        return None
    text = str(value)
    lower = text.lower()
    sensitive_keywords = ["password", "otp", "token", "secret", "key", "credential"]
    for kw in sensitive_keywords:
        if kw in lower:
            return "[REDACTED]"
    return text

