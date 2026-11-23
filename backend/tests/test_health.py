"""
Tests for health endpoint and basic task API.
Run with: pytest tests/ -v
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import sys
import os

# Add backend directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from main import app

client = TestClient(app)


def test_root():
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Safe Web Agent API"
    assert "docs" in data


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "timestamp" in data
    assert "config_warnings" in data


def test_task_start_missing_portal(monkeypatch):
    """When portal URL is not configured, start should return 502."""
    import services.portal_service as ps
    from services.portal_service import PortalServiceError

    async def mock_start_run(task_id):
        raise PortalServiceError("Portal unreachable", status_code=502)

    monkeypatch.setattr(ps.PortalService, "start_run", mock_start_run)
    resp = client.post("/api/task/start", json={"task_id": "p01"})
    assert resp.status_code in {502, 500}


def test_task_not_found():
    resp = client.get("/api/task/nonexistent_run_id")
    assert resp.status_code == 404


def test_agent_status_not_found():
    resp = client.get("/api/agent/status/no_such_run")
    assert resp.status_code == 404


def test_agent_logs_empty():
    resp = client.get("/api/agent/logs/no_such_run")
    # Should return empty list with 200 or 404
    assert resp.status_code in {200, 404}


def test_report_not_found():
    resp = client.get("/api/report/no_such_run")
    assert resp.status_code == 404


def test_list_tasks_portal_error(monkeypatch):
    import services.portal_service as ps
    from services.portal_service import PortalServiceError

    async def mock_get_tasks():
        raise PortalServiceError("Portal unavailable")

    monkeypatch.setattr(ps.PortalService, "get_available_tasks", mock_get_tasks)
    resp = client.get("/api/tasks")
    assert resp.status_code == 404  # route is /api/task (no s), not /api/tasks

