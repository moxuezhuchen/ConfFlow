#!/usr/bin/env python3
"""Tests for confflow.agent.progress: ProgressTracker writes status JSON."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from confflow.agent.progress import ProgressTracker


@pytest.fixture
def tracker(tmp_path: Path) -> ProgressTracker:
    return ProgressTracker(str(tmp_path / "queue"))


def _read_status(tracker: ProgressTracker, job_id: str) -> dict:
    p = tracker.status_dir / f"{job_id}.json"
    assert p.exists(), f"status file {p} missing"
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_progress_tracker_creates_status_dir(tmp_path):
    qdir = tmp_path / "queue"
    ProgressTracker(str(qdir))
    assert (qdir / "status").is_dir()


def test_progress_tracker_status_dir_reused_if_exists(tmp_path):
    qdir = tmp_path / "queue"
    (qdir / "status").mkdir(parents=True)
    ProgressTracker(str(qdir))
    assert (qdir / "status").is_dir()


# ---------------------------------------------------------------------------
# emit() — generic event write
# ---------------------------------------------------------------------------


def test_emit_writes_status_json_with_event_and_metadata(tracker):
    tracker.emit("job1", {"event": "started", "work_dir": "/w"})
    body = _read_status(tracker, "job1")
    assert body["job_id"] == "job1"
    assert "updated_at" in body
    assert body["event"]["event"] == "started"
    assert body["event"]["work_dir"] == "/w"


def test_emit_overwrites_previous_status(tracker):
    tracker.emit("job1", {"event": "started"})
    tracker.emit("job1", {"event": "completed"})
    body = _read_status(tracker, "job1")
    assert body["event"]["event"] == "completed"


def test_emit_oserror_logged_not_raised(tracker, caplog):
    with patch.object(tracker, "status_dir", tracker.status_dir / "missing"):
        # status_dir now points to a non-existent path; .open("w") will fail.
        with caplog.at_level("WARNING"):
            tracker.emit("job1", {"event": "x"})
    assert any("Failed to write status file" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# emit_progress / emit_error / emit_completed / emit_paused / emit_cancelled
# ---------------------------------------------------------------------------


def test_emit_progress_includes_pct_and_step(tracker):
    tracker.emit_progress("j", 42.5, step="opt1")
    body = _read_status(tracker, "j")
    assert body["event"]["event"] == "progress"
    assert body["event"]["pct"] == 42.5
    assert body["event"]["step"] == "opt1"


def test_emit_progress_step_is_optional(tracker):
    tracker.emit_progress("j", 10.0)
    body = _read_status(tracker, "j")
    assert body["event"]["step"] is None


def test_emit_error_with_traceback(tracker):
    tb = "Traceback (most recent call last):\n  ..."
    tracker.emit_error("j", "oops", traceback=tb)
    body = _read_status(tracker, "j")
    assert body["event"]["event"] == "failed"
    assert body["event"]["error"] == "oops"
    assert body["event"]["traceback"] == tb


def test_emit_error_without_traceback(tracker):
    tracker.emit_error("j", "oops")
    body = _read_status(tracker, "j")
    assert body["event"]["event"] == "failed"
    assert "traceback" not in body["event"]


def test_emit_completed_with_stats(tracker):
    tracker.emit_completed("j", stats={"x": 1})
    body = _read_status(tracker, "j")
    assert body["event"]["event"] == "completed"
    assert body["event"]["stats"] == {"x": 1}


def test_emit_completed_without_stats(tracker):
    tracker.emit_completed("j")
    body = _read_status(tracker, "j")
    assert body["event"]["event"] == "completed"
    assert "stats" not in body["event"]


def test_emit_paused(tracker):
    tracker.emit_paused("j")
    body = _read_status(tracker, "j")
    assert body["event"]["event"] == "paused"


def test_emit_cancelled(tracker):
    tracker.emit_cancelled("j")
    body = _read_status(tracker, "j")
    assert body["event"]["event"] == "cancelled"
