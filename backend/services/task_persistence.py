"""
SQLite Task and Approval Persistence for CampusPilot TaskManager.
Ensures task state, execution history, and approval records persist across server restarts.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config import settings

logger = logging.getLogger(__name__)

_DB_LOCK = threading.Lock()


class TaskPersistence:
    """Manages SQLite storage for task runs and human approval requests."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path:
            self.db_path = Path(db_path).resolve()
        else:
            default_path = getattr(settings, "DATABASE_PATH", None)
            if default_path:
                self.db_path = Path(default_path).resolve()
            else:
                self.db_path = (Path(__file__).parent.parent / "data" / "tasks.db").resolve()

        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create task_runs and task_approvals tables if they don't exist."""
        with _DB_LOCK:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS task_runs (
                        run_id TEXT PRIMARY KEY,
                        task_id TEXT,
                        status TEXT NOT NULL,
                        result_status TEXT,
                        result_summary TEXT,
                        error TEXT,
                        data_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS task_approvals (
                        approval_request_id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL,
                        action TEXT,
                        description TEXT,
                        status TEXT NOT NULL,
                        approved INTEGER,
                        created_at TEXT NOT NULL,
                        resolved_at TEXT,
                        FOREIGN KEY (run_id) REFERENCES task_runs(run_id)
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_runs_status ON task_runs(status)")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_approvals_run ON task_approvals(run_id)")
                conn.commit()
            except Exception as exc:
                logger.error("Failed to initialize SQLite database: %s", exc)
            finally:
                conn.close()

    def save_run(self, run_id: str, data: dict[str, Any]) -> None:
        """Persist or update a task run."""
        # Sanitize non-serializable objects (browser, asyncio.Event, etc.)
        sanitized = {}
        for k, v in data.items():
            if k in {"browser", "_approval_event"}:
                continue
            sanitized[k] = v

        try:
            data_json = json.dumps(sanitized, default=str)
        except Exception as exc:
            logger.warning("Error serializing run %s: %s", run_id, exc)
            data_json = "{}"

        now = datetime.now(tz=timezone.utc).isoformat()
        status = data.get("status", "pending")
        task_id = data.get("task_id")
        result_status = data.get("result_status")
        result_summary = data.get("result_summary")
        error = data.get("error")
        created_at = data.get("started_at") or now

        with _DB_LOCK:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO task_runs (run_id, task_id, status, result_status, result_summary, error, data_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id) DO UPDATE SET
                        status = excluded.status,
                        result_status = excluded.result_status,
                        result_summary = excluded.result_summary,
                        error = excluded.error,
                        data_json = excluded.data_json,
                        updated_at = excluded.updated_at
                """, (run_id, task_id, status, result_status, result_summary, error, data_json, created_at, now))
                conn.commit()
            except Exception as exc:
                logger.error("Failed to persist run %s: %s", run_id, exc)
            finally:
                conn.close()

    def load_all_runs(self) -> dict[str, dict[str, Any]]:
        """Load all saved task runs from database on startup."""
        runs: dict[str, dict[str, Any]] = {}
        with _DB_LOCK:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT run_id, data_json FROM task_runs")
                for row in cursor.fetchall():
                    try:
                        data = json.loads(row["data_json"])
                        runs[row["run_id"]] = data
                    except Exception:
                        pass
            except Exception as exc:
                logger.error("Failed to load runs from database: %s", exc)
            finally:
                conn.close()
        return runs

    def save_approval_request(
        self,
        approval_request_id: str,
        run_id: str,
        action: str,
        description: str,
        status: str = "pending",
        approved: Optional[bool] = None,
    ) -> None:
        """Persist an approval request."""
        now = datetime.now(tz=timezone.utc).isoformat()
        appr_val = 1 if approved is True else (0 if approved is False else None)
        with _DB_LOCK:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO task_approvals (approval_request_id, run_id, action, description, status, approved, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(approval_request_id) DO UPDATE SET
                        status = excluded.status,
                        approved = excluded.approved,
                        resolved_at = ?
                """, (approval_request_id, run_id, action, description, status, appr_val, now, now if approved is not None else None))
                conn.commit()
            except Exception as exc:
                logger.error("Failed to save approval request: %s", exc)
            finally:
                conn.close()

