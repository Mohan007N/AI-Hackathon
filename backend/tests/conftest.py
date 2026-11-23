"""
Global pytest configuration and fixtures for CampusPilot backend test suite.
Provides local mock portal lifecycle fixtures, test client, and environment configurations.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

# Ensure backend root is on sys.path
_BACKEND_DIR = Path(__file__).parent.parent.resolve()
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# Default environment configuration for tests
os.environ.setdefault("PORTAL_URL", "http://127.0.0.1:8000/test-portal")
os.environ.setdefault("BROWSER_HEADLESS", "false")
os.environ.setdefault("DATABASE_PATH", str(_BACKEND_DIR / "data" / "test_tasks.db"))

from main import app


@pytest.fixture(scope="session")
def test_client():
    """Shared TestClient for FastAPI endpoints."""
    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="session")
def mock_portal_base_url():
    """Returns the base URL for the local controlled mock portal."""
    return "http://127.0.0.1:8000/test-portal"


@pytest.fixture(autouse=True)
def ensure_test_dirs():
    """Ensure test log and data directories exist before each test."""
    (_BACKEND_DIR / "logs").mkdir(parents=True, exist_ok=True)
    (_BACKEND_DIR / "data").mkdir(parents=True, exist_ok=True)
    yield
