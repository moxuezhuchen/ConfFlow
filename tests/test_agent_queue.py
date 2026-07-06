#!/usr/bin/env python3
"""Tests for confflow.agent.queue: JobSpec and JobQueue (file-based watchdog)."""

from __future__ import annotations

import json
from pathlib import Path

from confflow.agent.queue import (
    DONE_DIR,
    INCOMING_DIR,
    PENDING_DIR,
    JobQueue,
    JobSpec,
)


def _make_spec(job_id: str, **overrides) -> JobSpec:
    defaults = dict(
        job_id=job_id,
        config_file="/tmp/config.yaml",
        input_xyz="/tmp/input.xyz",
        submitted_at="2026-01-01T00:00:00Z",
        submitted_by="tester",
    )
    defaults.update(overrides)
    return JobSpec(**defaults)


# ---------------------------------------------------------------------------
# JobSpec — constructor / dict round-trip / file IO
# ---------------------------------------------------------------------------


def test_jobspec_default_submitted_by():
    spec = JobSpec(
        job_id="j1",
        config_file="c.yaml",
        input_xyz="i.xyz",
        submitted_at="2026-01-01T00:00:00Z",
    )
    assert spec.submitted_by == "unknown"


def test_jobspec_to_dict_contains_all_fields():
    spec = _make_spec("j1")
    data = spec.to_dict()
    assert data == {
        "job_id": "j1",
        "config_file": "/tmp/config.yaml",
        "input_xyz": "/tmp/input.xyz",
        "submitted_at": "2026-01-01T00:00:00Z",
        "submitted_by": "tester",
    }


def test_jobspec_from_file_round_trip(tmp_path):
    spec = _make_spec("j1")
    p = tmp_path / "j1.json"
    p.write_text(json.dumps(spec.to_dict()), encoding="utf-8")

    loaded = JobSpec.from_file(str(p))
    assert loaded.job_id == spec.job_id
    assert loaded.config_file == spec.config_file
    assert loaded.input_xyz == spec.input_xyz
    assert loaded.submitted_at == spec.submitted_at
    assert loaded.submitted_by == spec.submitted_by


def test_jobspec_from_file_defaults_missing_optionals(tmp_path):
    p = tmp_path / "minimal.json"
    p.write_text(
        json.dumps(
            {
                "job_id": "j2",
                "config_file": "c.yaml",
                "input_xyz": "i.xyz",
                "submitted_at": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    loaded = JobSpec.from_file(str(p))
    assert loaded.submitted_by == "unknown"


# ---------------------------------------------------------------------------
# JobQueue — directory setup / enqueue / iter_new_jobs / move semantics
# ---------------------------------------------------------------------------


def test_queue_creates_subdirectories(tmp_path):
    qdir = tmp_path / "queue"
    JobQueue(str(qdir))
    assert (qdir / INCOMING_DIR).is_dir()
    assert (qdir / PENDING_DIR).is_dir()
    assert (qdir / DONE_DIR).is_dir()


def test_queue_enqueue_writes_json_file(tmp_path):
    q = JobQueue(str(tmp_path / "queue"))
    spec = _make_spec("alpha")
    path = q.enqueue(spec)
    assert Path(path).exists()
    body = json.loads(Path(path).read_text(encoding="utf-8"))
    assert body["job_id"] == "alpha"
    assert body["config_file"] == "/tmp/config.yaml"


def test_queue_iter_new_jobs_moves_incoming_to_pending(tmp_path):
    qdir = tmp_path / "queue"
    q = JobQueue(str(qdir))
    q.enqueue(_make_spec("job1"))
    q.enqueue(_make_spec("job2"))

    incoming = qdir / INCOMING_DIR
    pending = qdir / PENDING_DIR
    assert len(list(incoming.glob("*.json"))) == 2
    assert len(list(pending.glob("*.json"))) == 0

    specs = list(q.iter_new_jobs())

    assert sorted(s.job_id for s in specs) == ["job1", "job2"]
    # Files should have moved to pending
    assert len(list(incoming.glob("*.json"))) == 0
    assert sorted(p.name for p in pending.glob("*.json")) == ["job1.json", "job2.json"]


def test_queue_iter_new_jobs_is_idempotent_after_pickup(tmp_path):
    q = JobQueue(str(tmp_path / "queue"))
    q.enqueue(_make_spec("j1"))

    first = list(q.iter_new_jobs())
    second = list(q.iter_new_jobs())

    assert len(first) == 1
    assert len(second) == 0  # Already moved


def test_queue_iter_new_jobs_skips_corrupt_json(tmp_path):
    qdir = tmp_path / "queue"
    q = JobQueue(str(qdir))
    # Write a broken JSON file directly to incoming/
    (qdir / INCOMING_DIR / "broken.json").write_text("{not valid json", encoding="utf-8")
    q.enqueue(_make_spec("good"))

    specs = list(q.iter_new_jobs())

    assert [s.job_id for s in specs] == ["good"]
    # Corrupt file should NOT have been moved to pending
    assert not (qdir / PENDING_DIR / "broken.json").exists()
    # And the good one was moved
    assert (qdir / PENDING_DIR / "good.json").exists()


def test_queue_get_pending_returns_specs(tmp_path):
    q = JobQueue(str(tmp_path / "queue"))
    q.enqueue(_make_spec("a"))
    q.enqueue(_make_spec("b"))
    list(q.iter_new_jobs())  # Move them to pending

    pending = q.get_pending()
    assert sorted(s.job_id for s in pending) == ["a", "b"]


def test_queue_get_pending_skips_corrupt_files(tmp_path):
    qdir = tmp_path / "queue"
    q = JobQueue(str(qdir))  # creates dirs
    (qdir / PENDING_DIR / "corrupt.json").write_text("garbage", encoding="utf-8")

    pending = q.get_pending()
    assert pending == []


def test_queue_mark_done_moves_from_pending_to_done(tmp_path):
    qdir = tmp_path / "queue"
    q = JobQueue(str(qdir))
    q.enqueue(_make_spec("done1"))
    list(q.iter_new_jobs())

    q.mark_done("done1")

    assert not (qdir / PENDING_DIR / "done1.json").exists()
    assert (qdir / DONE_DIR / "done1.json").exists()


def test_queue_mark_done_noop_when_missing(tmp_path):
    q = JobQueue(str(tmp_path / "queue"))
    # Should silently do nothing — no exception.
    q.mark_done("does-not-exist")


def test_queue_mark_failed_only_logs(caplog):
    q = JobQueue("/tmp/_no_such_dir_for_failure_only")
    # mark_failed only logs; even with non-existent dir it should not crash
    q.mark_failed("any-id")


def test_queue_stop_sets_running_false(tmp_path):
    q = JobQueue(str(tmp_path))
    # _running starts False — calling stop() should not error.
    q.stop()
    assert q._running is False


def test_queue_watch_iterates_once_per_iteration_with_stub_callback(tmp_path, monkeypatch):
    """Watch loop should call the callback for each new job and exit on stop()."""
    qdir = tmp_path / "queue"
    q = JobQueue(str(qdir), poll_interval=0.01)

    # Replace time.sleep with a loop-friendly shim that drains incoming
    # every "sleep" tick and exits when stop is set.
    sleeps = {"n": 0}

    def fake_sleep(_t):
        sleeps["n"] += 1
        if sleeps["n"] >= 2:
            q.stop()

    monkeypatch.setattr("confflow.agent.queue.time.sleep", fake_sleep)

    seen = []

    def cb(spec):
        seen.append(spec.job_id)

    # Drop a job before watch starts
    q.enqueue(_make_spec("late"))
    q.watch(cb)

    assert seen == ["late"]
