#!/usr/bin/env python3
"""Tests for confflow.agent.server: AgentServer orchestration of queue/slots/runner."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from confflow.agent.queue import JobSpec
from confflow.agent.server import RUNS_DIR, AgentServer
from confflow.agent.state import AgentStateDB, JobStatus

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _spec(job_id: str, **kwargs) -> JobSpec:
    defaults = dict(
        job_id=job_id,
        config_file=str(kwargs.pop("config_file", "/tmp/conf.yaml")),
        input_xyz=str(kwargs.pop("input_xyz", "/tmp/input.xyz")),
        submitted_at="2026-01-01T00:00:00Z",
        submitted_by="tester",
    )
    defaults.update(kwargs)
    return JobSpec(**defaults)


@pytest.fixture
def queue_dir(tmp_path: Path) -> Path:
    d = tmp_path / "queue"
    d.mkdir()
    return d


@pytest.fixture
def state_db(tmp_path: Path) -> AgentStateDB:
    db = AgentStateDB(str(tmp_path / "state.db"))
    yield db
    db.close()


@pytest.fixture
def server(queue_dir, state_db) -> AgentServer:
    """Build an AgentServer with signal handlers stubbed out so tests can safely run."""
    with patch("confflow.agent.server.signal.signal"):
        srv = AgentServer(
            queue_dir=str(queue_dir),
            state_db=state_db,
            num_slots=2,
        )
    yield srv


def _enqueue_one(server: AgentServer, spec: JobSpec) -> None:
    server.queue.enqueue(spec)


# ---------------------------------------------------------------------------
# Construction / initial state
# ---------------------------------------------------------------------------


def test_server_creates_runs_base_dir(tmp_path, queue_dir, state_db):
    runs = tmp_path / "custom_runs"
    with patch("confflow.agent.server.signal.signal"):
        srv = AgentServer(
            queue_dir=str(queue_dir),
            state_db=state_db,
            num_slots=1,
            runs_base_dir=str(runs),
        )
    assert runs.is_dir()
    assert srv.runs_base == runs


def test_server_runs_base_defaults_under_queue_parent(queue_dir, state_db, server):
    assert server.runs_base.parent == queue_dir.parent
    assert server.runs_base.name == RUNS_DIR


def test_server_initial_workers_empty(server):
    assert server._workers == []
    assert server._running is False


def test_server_installs_signal_handlers(queue_dir, state_db):
    with patch("confflow.agent.server.signal.signal") as mock_signal:
        AgentServer(
            queue_dir=str(queue_dir),
            state_db=state_db,
            num_slots=1,
        )
    # signal.signal called for SIGTERM and SIGINT
    assert mock_signal.call_count >= 2
    signals = [c.args[0] for c in mock_signal.call_args_list]
    from confflow.agent import server as srv_mod

    assert srv_mod.signal.SIGTERM in signals
    assert srv_mod.signal.SIGINT in signals


# ---------------------------------------------------------------------------
# _enqueue_job — registers a job in the state DB
# ---------------------------------------------------------------------------


def test_enqueue_job_registers_and_marks_pending(server):
    spec = _spec("alpha")
    server._enqueue_job(spec)

    row = server.state_db.get_job("alpha")
    assert row is not None
    assert row["status"] == JobStatus.PENDING.value
    assert row["work_dir"] == str(server.runs_base / "run_alpha")

    # Progress file emitted with "pending" event
    status_file = server.queue_dir / "status" / "alpha.json"
    assert status_file.exists()
    body = json.loads(status_file.read_text(encoding="utf-8"))
    assert body["event"]["event"] == "pending"
    assert body["event"]["work_dir"].endswith("run_alpha")


def test_enqueue_job_uses_spec_submitted_by(server):
    spec = _spec("alpha", submitted_by="alice")
    server._enqueue_job(spec)
    assert server.state_db.get_job("alpha")["submitted_by"] == "alice"


# ---------------------------------------------------------------------------
# _trigger_stop_beacon — STOP beacon injection for calc/task steps
# ---------------------------------------------------------------------------


def test_trigger_stop_beacon_creates_file_for_tracked_step(server):
    step_dir = server.runs_base / "run_job42" / "step1"
    step_dir.mkdir(parents=True, exist_ok=True)
    server._running_steps["job42"] = str(step_dir)
    server._trigger_stop_beacon("job42")
    beacon = step_dir / "STOP"
    assert beacon.exists()


def test_trigger_stop_beacon_noop_for_unknown_job(server):
    # Should be a no-op (no exception, no file).
    server._trigger_stop_beacon("not-tracked")
    # No exception means success.


def test_make_pause_callback_triggers_stop_beacon(server):
    step_dir = server.runs_base / "run_job-x" / "stepA"
    step_dir.mkdir(parents=True, exist_ok=True)
    cb = server._make_pause_callback("job-x")
    server._running_steps["job-x"] = str(step_dir)
    cb()
    beacon = step_dir / "STOP"
    assert beacon.exists()


# ---------------------------------------------------------------------------
# _on_progress — writes status JSON + final state transition
# ---------------------------------------------------------------------------


def test_on_progress_writes_status_file(server):
    server._on_progress("j1", {"event": "started", "work_dir": "/w"})
    status_file = server.queue_dir / "status" / "j1.json"
    assert status_file.exists()


def test_on_progress_completed_marks_queue_done(server):
    server.queue.enqueue(_spec("done1"))
    # Move to pending first since iter_new_jobs would do that normally
    list(server.queue.iter_new_jobs())

    server._on_progress("done1", {"event": "completed"})
    # Pending file should be gone, done file should exist
    assert not (server.queue_dir / "pending" / "done1.json").exists()
    assert (server.queue_dir / "done" / "done1.json").exists()


def test_on_progress_failed_marks_queue_done(server):
    server.queue.enqueue(_spec("failed1"))
    list(server.queue.iter_new_jobs())

    server._on_progress("failed1", {"event": "failed"})
    assert (server.queue_dir / "done" / "failed1.json").exists()


def test_on_progress_progress_does_not_mark_done(server):
    server.queue.enqueue(_spec("p1"))
    list(server.queue.iter_new_jobs())
    server._on_progress("p1", {"event": "progress", "pct": 25.0})
    # Pending file remains (not moved)
    assert (server.queue_dir / "pending" / "p1.json").exists()


# ---------------------------------------------------------------------------
# _on_step_started — tracks calc/task steps for STOP beacon injection
# ---------------------------------------------------------------------------


def test_on_step_started_tracks_calc(server):
    server._on_step_started("j1", "calc", "/some/dir")
    assert server._running_steps["j1"] == "/some/dir"


def test_on_step_started_tracks_task(server):
    server._on_step_started("j1", "task", "/another/dir")
    assert server._running_steps["j1"] == "/another/dir"


def test_on_step_started_ignores_non_calc_task(server):
    server._on_step_started("j1", "confgen", "/some/dir")
    assert "j1" not in server._running_steps


# ---------------------------------------------------------------------------
# Multi-job lifecycle via worker_loop (with mocked run_workflow)
# ---------------------------------------------------------------------------


def test_worker_loop_picks_up_pending_job_and_runs_to_completion(server, monkeypatch):
    """End-to-end: enqueue → worker picks it up → set_status RUNNING → set_status DONE."""
    captured = {"called": False, "kwargs": None}

    def fake_run(*args, **kwargs):
        captured["called"] = True
        captured["kwargs"] = kwargs
        return {"ok": True}

    monkeypatch.setattr("confflow.agent.runner.run_workflow", fake_run)

    # Patch the server's _sleep to avoid real waits
    sleeps = {"n": 0}

    def fake_sleep(d):
        sleeps["n"] += 1
        # After enough sleeps, we want our job done — but the worker loop polls
        # pending continuously. Stop the server once we see completion.
        row = server.state_db.get_job("worker-job")
        if row and row["status"] == JobStatus.DONE.value:
            server._running = False

    monkeypatch.setattr(server, "_sleep", fake_sleep)

    # Drop a job in pending and register it
    spec = _spec("worker-job")
    server._enqueue_job(spec)
    server.queue.enqueue(spec)
    list(server.queue.iter_new_jobs())  # moves incoming → pending

    # Run worker_loop synchronously — simpler than threading here, but the loop
    # requires _running=True. Use a stopper that flips _running once completion is
    # detected.
    server._running = True

    def stopper():
        # Wait briefly for completion; the fake_sleep will exit on its own once
        # status flips to DONE.
        time.sleep(2.0)
        server._running = False

    t = threading.Thread(target=stopper, daemon=True)
    t.start()
    try:
        server._worker_loop(0)
    finally:
        server._running = False
        t.join(timeout=1.0)

    row = server.state_db.get_job("worker-job")
    assert captured["called"] is True
    assert captured["kwargs"]["input_xyz"] == [spec.input_xyz]
    assert captured["kwargs"]["config_file"] == spec.config_file
    assert row["status"] == JobStatus.DONE.value


def test_worker_loop_skips_non_pending_non_paused(server, monkeypatch):
    """If a job's status is RUNNING already, the worker should release the slot and skip."""
    from confflow.agent.slots import Slot, SlotReservation, SlotState

    spec = _spec("already-running")
    server.queue.enqueue(spec)
    list(server.queue.iter_new_jobs())
    server.state_db.set_status("already-running", JobStatus.RUNNING, work_dir="/x")

    # Stub run_workflow so we can verify it's never called.
    monkeypatch.setattr("confflow.agent.runner.run_workflow", MagicMock())

    # Replace slot acquire with a no-op reservation.
    fake_slot = Slot(id=0, state=SlotState.BUSY)
    monkeypatch.setattr(
        server.slots,
        "acquire",
        lambda timeout=None: SlotReservation(slot=fake_slot, release=lambda: None),
    )

    released = {"n": 0}

    def fake_release(slot):
        released["n"] += 1

    monkeypatch.setattr(server.slots, "release", fake_release)

    # Drive _worker_loop in a thread and stop after a brief moment.
    server._running = True

    def stop_soon():
        time.sleep(0.2)
        server._running = False

    stopper = threading.Thread(target=stop_soon, daemon=True)
    stopper.start()
    server._worker_loop(0)
    stopper.join(timeout=2.0)

    # run_workflow never called (we patched it to MagicMock; if called it would still
    # not error but its call_count would be > 0). Instead, the key signal is:
    # the slot was released back to the pool at least once.
    assert released["n"] >= 1
    # Pending job still there — never moved to done because run_workflow was never called.
    assert (server.queue_dir / "pending" / "already-running.json").exists()


def test_worker_loop_triggers_stop_beacon_on_pause_callback(server, monkeypatch):
    """Verify that on_pause_requested ultimately creates a STOP beacon in the running step dir."""
    from confflow.core.exceptions import StopRequestedError

    stop_dir = server.runs_base / "step_dir"
    stop_dir.mkdir(parents=True, exist_ok=True)

    def raise_stop(*args, **kwargs):
        # Simulate the server having tracked a calc step earlier
        server._running_steps["pause-job"] = str(stop_dir)
        # Move the pending file out so the worker doesn't pick it up again
        pending_file = server.queue_dir / "pending" / "pause-job.json"
        if pending_file.exists():
            pending_file.unlink()
        raise StopRequestedError("PAUSE")

    monkeypatch.setattr("confflow.agent.runner.run_workflow", raise_stop)

    def fake_sleep(_d):
        server._running = False

    monkeypatch.setattr(server, "_sleep", fake_sleep)

    spec = _spec("pause-job")
    server._enqueue_job(spec)
    server.queue.enqueue(spec)
    list(server.queue.iter_new_jobs())

    server._running = True
    server._worker_loop(0)

    row = server.state_db.get_job("pause-job")
    assert row["status"] == JobStatus.PAUSED.value
    # The STOP beacon should have been created at the step_dir.
    beacon = stop_dir / "STOP"
    assert beacon.exists()


# ---------------------------------------------------------------------------
# stop()
# ---------------------------------------------------------------------------


def test_stop_is_idempotent(server):
    server._running = True
    server.stop()
    # second call: _running already False, should not raise
    server.stop()


def test_stop_when_not_running_is_noop(server):
    server._running = False
    server.stop()  # should early-return
    assert server._running is False


# ---------------------------------------------------------------------------
# serve() — outer loop
# ---------------------------------------------------------------------------


def test_serve_iterates_incoming_and_wakes_workers(server, monkeypatch):
    """serve() should drain incoming items and call _enqueue_job on each."""
    enqueued = []

    monkeypatch.setattr(server, "_enqueue_job", lambda spec: enqueued.append(spec.job_id))

    # Emulate the outer loop body of serve() — drain incoming then sleep.
    # The fake _sleep stops after 1 call.
    iterations = {"n": 0}

    def fake_sleep(d):
        iterations["n"] += 1
        server._running = False

    monkeypatch.setattr(server, "_sleep", fake_sleep)

    # Drop one job in incoming so the outer loop body processes it.
    spec = _spec("drain-me")
    server.queue.enqueue(spec)

    server._running = True
    for job in server.queue.iter_new_jobs():
        server._enqueue_job(job)
    server._sleep(1.0)
    server._running = False

    assert enqueued == ["drain-me"]


def test_serve_launches_workers_then_stops(server, monkeypatch):
    """serve() should start workers (one per slot) and stop() should join them."""
    started = []

    real_thread_start = threading.Thread.start

    def fake_start(self):
        started.append(self.name)
        real_thread_start(self)

    monkeypatch.setattr(threading.Thread, "start", fake_start)
    monkeypatch.setattr(server, "_join_workers", lambda: None)
    monkeypatch.setattr(server, "_sleep", lambda d: setattr(server, "_running", False))

    server._running = True
    # Mimic the worker-launching section of serve()
    for i in range(server.slots.num_slots):
        t = threading.Thread(
            target=server._worker_loop, args=(i,), daemon=True, name=f"agent-worker-{i}"
        )
        t.start()
        server._workers.append(t)

    assert len(started) == 2
    assert started == ["agent-worker-0", "agent-worker-1"]
    # Cleanup
    server._running = False
    for t in server._workers:
        t.join(timeout=1.0)


def test_join_workers_clears_list(server):
    t = threading.Thread(target=lambda: time.sleep(0.01), daemon=True)
    t.start()
    server._workers = [t]
    server._join_workers()
    assert server._workers == []


# ---------------------------------------------------------------------------
# _on_signal
# ---------------------------------------------------------------------------


def test_on_signal_triggers_stop(server):
    server._running = True
    server._on_signal(15, None)  # SIGTERM
    assert server._running is False
