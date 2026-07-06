#!/usr/bin/env python3
"""Tests for confflow.agent.runner: JobRunner with mocked run_workflow."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from confflow.agent.runner import JobContext, JobRunner
from confflow.core.exceptions import StopRequestedError


@pytest.fixture
def ctx(tmp_path: Path):
    """Build a real AgentStateDB-backed JobContext for a single job."""
    from confflow.agent.state import AgentStateDB

    db = AgentStateDB(str(tmp_path / "state.db"))
    db.add_job(
        job_id="job",
        config_file=str(tmp_path / "conf.yaml"),
        input_xyz=str(tmp_path / "input.xyz"),
        submitted_at="2026-01-01T00:00:00Z",
        submitted_by="tester",
    )
    yield JobContext(
        job_id="job",
        config_file=str(tmp_path / "conf.yaml"),
        input_xyz=str(tmp_path / "input.xyz"),
        work_dir=str(tmp_path / "work"),
        pause_beacon_file=str(tmp_path / "work" / "PAUSE"),
        state_db=db,
    )
    db.close()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_job_runner_marks_done_on_success(ctx, monkeypatch):
    """On clean completion, status transitions to DONE and a 'completed' event is emitted."""
    events = []

    fake_run = MagicMock(return_value={"results": 5})
    monkeypatch.setattr("confflow.agent.runner.run_workflow", fake_run)

    ctx.on_progress = events.append

    JobRunner(ctx).run()

    row = ctx.state_db.get_job("job")
    assert row["status"] == "done"
    fake_run.assert_called_once()
    call_kwargs = fake_run.call_args.kwargs
    assert call_kwargs["input_xyz"] == [ctx.input_xyz]
    assert call_kwargs["config_file"] == ctx.config_file
    assert call_kwargs["work_dir"] == ctx.work_dir
    assert call_kwargs["pause_beacon_file"] == ctx.pause_beacon_file
    assert call_kwargs["resume"] is False
    assert call_kwargs["verbose"] is False
    assert call_kwargs["original_input_files"] is None

    event_names = [e["event"] for e in events]
    assert event_names == ["started", "completed"]


def test_job_runner_passes_step_started_callback(ctx, monkeypatch):
    """on_step_started must be passed through to run_workflow."""
    fake_run = MagicMock(return_value=None)
    monkeypatch.setattr("confflow.agent.runner.run_workflow", fake_run)

    def my_step_cb(name, stype, sdir):
        pass

    ctx.on_step_started = my_step_cb

    JobRunner(ctx).run()

    assert fake_run.call_args.kwargs["step_started_callback"] is my_step_cb


def test_job_runner_emits_started_event_with_work_dir(ctx, monkeypatch):
    events = []
    monkeypatch.setattr("confflow.agent.runner.run_workflow", MagicMock(return_value=None))
    ctx.on_progress = events.append

    JobRunner(ctx).run()

    started = events[0]
    assert started["event"] == "started"
    assert started["job_id"] == "job"
    assert started["work_dir"] == ctx.work_dir


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_job_runner_stop_requested_marks_paused_and_calls_pause_cb(ctx, monkeypatch):
    """StopRequestedError is special — it triggers PAUSED status and on_pause_requested."""
    pause_called = {"count": 0}

    def raise_stop(*args, **kwargs):
        raise StopRequestedError("PAUSE beacon detected")

    monkeypatch.setattr("confflow.agent.runner.run_workflow", raise_stop)

    events = []
    ctx.on_progress = events.append
    ctx.on_pause_requested = lambda: pause_called.update(count=pause_called["count"] + 1)

    JobRunner(ctx).run()

    row = ctx.state_db.get_job("job")
    assert row["status"] == "paused"
    assert pause_called["count"] == 1
    event_names = [e["event"] for e in events]
    assert event_names == ["started", "paused"]


def test_job_runner_generic_exception_marks_failed(ctx, monkeypatch):
    """Any non-StopRequestedError exception marks the job as failed with error_message."""
    events = []

    def boom(*args, **kwargs):
        raise RuntimeError("something exploded")

    monkeypatch.setattr("confflow.agent.runner.run_workflow", boom)
    ctx.on_progress = events.append

    JobRunner(ctx).run()

    row = ctx.state_db.get_job("job")
    assert row["status"] == "failed"
    assert "something exploded" in row["error_message"]
    event_names = [e["event"] for e in events]
    assert event_names == ["started", "failed"]
    failed_evt = events[-1]
    assert failed_evt["error"] == "something exploded"
    assert "Traceback" in failed_evt["traceback"]


def test_job_runner_emits_work_dir_before_run(ctx, monkeypatch):
    """set_status(RUNNING, work_dir=...) is called BEFORE run_workflow."""
    observed_work_dir = {}

    def fake_run(*args, **kwargs):
        # Inside run_workflow, status should already be RUNNING and work_dir set
        row = ctx.state_db.get_job("job")
        observed_work_dir["status"] = row["status"]
        observed_work_dir["work_dir"] = row["work_dir"]
        return None

    monkeypatch.setattr("confflow.agent.runner.run_workflow", fake_run)
    JobRunner(ctx).run()

    assert observed_work_dir["status"] == "running"
    assert observed_work_dir["work_dir"] == ctx.work_dir


# ---------------------------------------------------------------------------
# Progress callback isolation
# ---------------------------------------------------------------------------


def test_job_runner_continues_when_progress_callback_raises(ctx, monkeypatch):
    """A bad on_progress callback must not crash the runner."""
    monkeypatch.setattr("confflow.agent.runner.run_workflow", MagicMock(return_value=None))

    def bad_cb(event):
        if event["event"] == "completed":
            raise ValueError("callback down")

    ctx.on_progress = bad_cb

    # Should not raise
    JobRunner(ctx).run()

    row = ctx.state_db.get_job("job")
    assert row["status"] == "done"


def test_job_runner_no_callbacks_is_fine(ctx, monkeypatch):
    """When ctx.on_progress/on_pause_requested are None the runner still works."""
    monkeypatch.setattr("confflow.agent.runner.run_workflow", MagicMock(return_value=None))
    # All callbacks default to None in the fixture
    JobRunner(ctx).run()
    assert ctx.state_db.get_job("job")["status"] == "done"
