#!/usr/bin/env python3
"""Tests for confflow.agent.state: AgentStateDB, JobStatus, CLEAR sentinel."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from confflow.agent.state import CLEAR, AgentStateDB, JobStatus


@pytest.fixture
def db(tmp_path: Path) -> AgentStateDB:
    d = AgentStateDB(str(tmp_path / "state.db"))
    yield d
    d.close()


def _add(db: AgentStateDB, job_id: str = "j1", **kwargs) -> None:
    db.add_job(
        job_id=job_id,
        config_file=kwargs.get("config_file", "config.yaml"),
        input_xyz=kwargs.get("input_xyz", "input.xyz"),
        submitted_at=kwargs.get("submitted_at", "2026-01-01T00:00:00Z"),
        submitted_by=kwargs.get("submitted_by", "tester"),
    )


# ---------------------------------------------------------------------------
# JobStatus enum
# ---------------------------------------------------------------------------


def test_jobstatus_values_and_string_equality():
    assert JobStatus.PENDING.value == "pending"
    assert JobStatus.RUNNING.value == "running"
    assert JobStatus.PAUSED.value == "paused"
    assert JobStatus.DONE.value == "done"
    assert JobStatus.FAILED.value == "failed"
    assert JobStatus.CANCELLED.value == "cancelled"


def test_jobstatus_is_string_subtype():
    # JobStatus inherits from str so values compare equal to raw strings
    assert JobStatus.DONE == "done"
    assert JobStatus.PENDING in {"pending", "running"}


# ---------------------------------------------------------------------------
# CLEAR sentinel
# ---------------------------------------------------------------------------


def test_clear_sentinel_repr():
    assert repr(CLEAR) == "CLEAR"


def test_clear_sentinel_is_singleton():
    """Two references to module attribute should be the same object."""
    from confflow.agent import state as state_mod

    assert state_mod.CLEAR is CLEAR


# ---------------------------------------------------------------------------
# AgentStateDB — add_job
# ---------------------------------------------------------------------------


def test_add_job_inserts_row(db):
    _add(db, "jA")
    row = db.get_job("jA")
    assert row is not None
    assert row["job_id"] == "jA"
    assert row["config_file"] == "config.yaml"
    assert row["input_xyz"] == "input.xyz"
    assert row["status"] == "pending"  # default
    assert row["submitted_by"] == "tester"
    assert row["submitted_at"] == "2026-01-01T00:00:00Z"


def test_add_job_default_submitted_by(db):
    db.add_job(
        job_id="x",
        config_file="c",
        input_xyz="i",
        submitted_at="2026-01-01T00:00:00Z",
    )
    assert db.get_job("x")["submitted_by"] == "unknown"


def test_add_job_is_idempotent(db):
    _add(db, "once")
    _add(db, "once")  # second add with same id should not error
    rows = db.list_jobs()
    assert sum(1 for r in rows if r["job_id"] == "once") == 1


def test_add_job_creates_db_file(tmp_path):
    dbdir = tmp_path / "dbdir"
    dbdir.mkdir()
    db = AgentStateDB(str(dbdir / "nested" / "state.db"))
    try:
        assert (dbdir / "nested" / "state.db").exists()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# AgentStateDB — get_job / list_jobs
# ---------------------------------------------------------------------------


def test_get_job_returns_none_for_unknown(db):
    assert db.get_job("missing") is None


def test_list_jobs_returns_all(db):
    _add(db, "a")
    _add(db, "b")
    _add(db, "c")
    rows = db.list_jobs()
    assert sorted(r["job_id"] for r in rows) == ["a", "b", "c"]


def test_list_jobs_filter_by_status(db):
    _add(db, "p1")
    _add(db, "p2")
    _add(db, "r1")
    db.set_status("r1", JobStatus.RUNNING)

    pending = db.list_jobs(status=JobStatus.PENDING)
    running = db.list_jobs(status=JobStatus.RUNNING)
    missing = db.list_jobs(status=JobStatus.DONE)

    assert sorted(r["job_id"] for r in pending) == ["p1", "p2"]
    assert [r["job_id"] for r in running] == ["r1"]
    assert missing == []


# ---------------------------------------------------------------------------
# AgentStateDB — set_status (transitions / timestamps)
# ---------------------------------------------------------------------------


def test_set_status_running_sets_started_at(db):
    _add(db, "job")
    db.set_status("job", JobStatus.RUNNING)
    row = db.get_job("job")
    assert row["status"] == "running"
    assert row["started_at"] is not None
    assert row["started_at"].endswith("Z")


def test_set_status_running_does_not_overwrite_started_at(db):
    _add(db, "job")
    db.set_status("job", JobStatus.RUNNING)
    db.get_job("job")["started_at"]
    db.set_status("job", JobStatus.PAUSED)
    # started_at is preserved via COALESCE in subsequent status changes that don't touch it
    assert db.get_job("job")["status"] == "paused"


def test_set_status_done_sets_completed_at(db):
    _add(db, "job")
    db.set_status("job", JobStatus.DONE)
    assert db.get_job("job")["completed_at"] is not None


def test_set_status_failed_sets_completed_at(db):
    _add(db, "job")
    db.set_status("job", JobStatus.FAILED, error_message="boom")
    row = db.get_job("job")
    assert row["status"] == "failed"
    assert row["completed_at"] is not None
    assert row["error_message"] == "boom"


def test_set_status_cancelled_sets_completed_at(db):
    _add(db, "job")
    db.set_status("job", JobStatus.CANCELLED)
    assert db.get_job("job")["completed_at"] is not None


def test_set_status_paused_does_not_set_completed_at(db):
    _add(db, "job")
    db.set_status("job", JobStatus.PAUSED)
    row = db.get_job("job")
    assert row["status"] == "paused"
    assert row["completed_at"] is None


def test_set_status_work_dir_and_slot_id(db):
    _add(db, "job")
    db.set_status(
        "job",
        JobStatus.RUNNING,
        work_dir="/work",
        slot_id=3,
    )
    row = db.get_job("job")
    assert row["work_dir"] == "/work"
    assert row["slot_id"] == 3


# ---------------------------------------------------------------------------
# AgentStateDB — CLEAR sentinel behavior on partial updates
# ---------------------------------------------------------------------------


def test_clear_sentinel_does_not_touch_field_by_default(db):
    """Setting status without passing other fields should not wipe them."""
    _add(db, "job")
    # Set the row with extra
    db.set_status("job", JobStatus.RUNNING, work_dir="/wd", slot_id=2)
    # Mark current_step explicitly
    db.set_status("job", JobStatus.PAUSED, current_step="step1")

    # Now another update — should NOT clear current_step because CLEAR is the default
    db.set_status("job", JobStatus.RUNNING)

    row = db.get_job("job")
    assert row["current_step"] == "step1"


def test_clear_sentinel_explicit_none_clears_error_message(db):
    _add(db, "job")
    db.set_status("job", JobStatus.FAILED, error_message="boom")
    assert db.get_job("job")["error_message"] == "boom"

    db.set_status("job", JobStatus.PENDING, error_message=None)
    assert db.get_job("job")["error_message"] is None


def test_clear_sentinel_explicit_none_sets_progress_to_zero(db):
    _add(db, "job")
    db.set_status("job", JobStatus.RUNNING, progress_pct=42.5)
    assert db.get_job("job")["progress_pct"] == 42.5

    db.set_status("job", JobStatus.PAUSED, progress_pct=None)
    assert db.get_job("job")["progress_pct"] == 0.0


def test_clear_sentinel_explicit_none_clears_current_step(db):
    _add(db, "job")
    db.set_status("job", JobStatus.RUNNING, current_step="step-x")
    db.set_status("job", JobStatus.PAUSED, current_step=None)
    assert db.get_job("job")["current_step"] is None


def test_clear_sentinel_explicit_none_on_completed_at(db):
    _add(db, "job")
    db.set_status("job", JobStatus.DONE)
    assert db.get_job("job")["completed_at"] is not None
    db.set_status("job", JobStatus.PENDING, completed_at=None)
    assert db.get_job("job")["completed_at"] is None


# ---------------------------------------------------------------------------
# AgentStateDB — extra dict serialization
# ---------------------------------------------------------------------------


def test_extra_dict_is_serialized_as_json(db):
    _add(db, "job")
    db.set_status("job", JobStatus.RUNNING, extra={"foo": "bar", "n": 7})
    stored = db.get_job("job")["extra"]
    assert json.loads(stored) == {"foo": "bar", "n": 7}


def test_set_status_without_extra_keeps_existing_extra(db):
    _add(db, "job")
    db.set_status("job", JobStatus.RUNNING, extra={"k": "v"})
    db.set_status("job", JobStatus.PAUSED)
    assert json.loads(db.get_job("job")["extra"]) == {"k": "v"}
