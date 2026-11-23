"""
Tests for action logger.
"""

from __future__ import annotations

import os, sys, json, tempfile, pathlib
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest


def test_log_and_read(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config.settings, "LOG_DIRECTORY", str(tmp_path))

    import utils.action_logger as al
    al._locks.clear()   # reset thread locks

    entry = al.log_action(
        "test_run_001", 1, "navigate",
        target="Elective page",
        url="https://portal.example.com/electives",
        reason="Student requested elective check",
    )
    assert entry["action"] == "navigate"
    assert entry["step"] == 1

    logs = al.read_logs("test_run_001")
    assert len(logs) == 1
    assert logs[0]["action"] == "navigate"


def test_no_password_logged(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config.settings, "LOG_DIRECTORY", str(tmp_path))

    import utils.action_logger as al
    al._locks.clear()

    # Caller must sanitize — sanitize_value helper should catch obvious keys
    sanitized = al.sanitize_value("user_password=secret123")
    assert sanitized == "[REDACTED]"

    safe = al.sanitize_value("OE123 - Data Science")
    assert safe == "OE123 - Data Science"


def test_empty_action_raises():
    import utils.action_logger as al
    with pytest.raises(ValueError):
        al.log_action("run_x", 1, "")


def test_read_nonexistent_run(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config.settings, "LOG_DIRECTORY", str(tmp_path))
    import utils.action_logger as al
    al._locks.clear()

    logs = al.read_logs("nonexistent_run_id")
    assert logs == []

