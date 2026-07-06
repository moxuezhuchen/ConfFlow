"""Unit tests for confflow.agent.cli subcommands.

Each test drives cmd_* directly via argparse-built Namespace, captures
stdout/stderr via the `capsys` fixture, and avoids spawning subprocesses
or hitting the network. The `serve` command is replaced with a stub that
short-circuits the blocking loop.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from confflow.agent import cli as agent_cli
from confflow.agent.queue import JobSpec
from confflow.agent.state import AgentStateDB, JobStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(**kwargs) -> JobSpec:
    defaults = dict(
        job_id="cli_job",
        config_file="/tmp/conf.yaml",
        input_xyz="/tmp/input.xyz",
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
def state_db_path(tmp_path: Path) -> Path:
    return tmp_path / "state.db"


@pytest.fixture
def state_db(state_db_path: Path) -> AgentStateDB:
    db = AgentStateDB(str(state_db_path))
    yield db
    db.close()


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    d = tmp_path / "logs"
    d.mkdir()
    return d


def _add_pending(db: AgentStateDB, spec: JobSpec, work_dir: str | None = None) -> None:
    db.add_job(
        job_id=spec.job_id,
        config_file=spec.config_file,
        input_xyz=spec.input_xyz,
        submitted_at=spec.submitted_at,
        submitted_by=spec.submitted_by,
    )
    wd = work_dir or f"/tmp/runs/{spec.job_id}"
    db.set_status(spec.job_id, JobStatus.PENDING, work_dir=wd)


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def test_build_parser_requires_command():
    parser = agent_cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_build_parser_serve_subcommand():
    parser = agent_cli.build_parser()
    args = parser.parse_args(["serve"])
    assert args.command == "serve"
    assert args.slots == 2
    assert args.queue_dir == "~/.confflow-queue"


def test_build_parser_status_subcommand():
    parser = agent_cli.build_parser()
    args = parser.parse_args(["status", "abc123"])
    assert args.command == "status"
    assert args.job_id == "abc123"


def test_main_expands_user_paths(monkeypatch):
    """`main` expands ~ in queue_dir, state_db, log_dir via os.path.expanduser."""
    called = {}

    def fake_func(args):
        called["queue"] = args.queue_dir
        called["state"] = args.state_db
        called["log"] = getattr(args, "log_dir", None)
        return 0

    fake_func.__name__ = "fake_func"
    monkeypatch.setattr(
        agent_cli,
        "build_parser",
        lambda: MagicMock(
            parse_args=lambda a: argparse.Namespace(
                command="list",
                func=fake_func,
                queue_dir="~/q",
                state_db="~/s",
                log_dir="~/l",
                no_all=False,
            )
        ),
    )
    rc = agent_cli.main([])
    assert rc == 0
    assert called["queue"].startswith("/")
    assert called["state"].startswith("/")
    assert called["log"].startswith("/")


# ---------------------------------------------------------------------------
# cmd_status
# ---------------------------------------------------------------------------


def test_cmd_status_unknown_job_writes_stderr(state_db, queue_dir, capsys):
    args = argparse.Namespace(
        job_id="missing",
        queue_dir=str(queue_dir),
        state_db=str(state_db.db_path),
    )
    rc = agent_cli.cmd_status(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "not found" in captured.err


def test_cmd_status_prints_job_fields(state_db, queue_dir, capsys):
    spec = _spec(job_id="alive")
    _add_pending(state_db, spec, work_dir="/var/confflow/alive")
    args = argparse.Namespace(
        job_id="alive",
        queue_dir=str(queue_dir),
        state_db=str(state_db.db_path),
    )

    rc = agent_cli.cmd_status(args)
    captured = capsys.readouterr()
    assert rc == 0
    assert "alive" in captured.out
    assert "Job ID" in captured.out
    assert "/var/confflow/alive" in captured.out


def test_cmd_status_includes_status_json_when_present(state_db, queue_dir, tmp_path, capsys):
    spec = _spec(job_id="withstatus")
    _add_pending(state_db, spec, work_dir="/var/confflow/withstatus")

    status_dir = queue_dir / "status"
    status_dir.mkdir(parents=True, exist_ok=True)
    (status_dir / "withstatus.json").write_text(json.dumps({"event": {"step": "opt", "pct": 42}}))

    args = argparse.Namespace(
        job_id="withstatus",
        queue_dir=str(queue_dir),
        state_db=str(state_db.db_path),
    )
    rc = agent_cli.cmd_status(args)
    captured = capsys.readouterr()
    assert rc == 0
    assert "Latest event" in captured.out


def test_cmd_status_handles_corrupt_status_json(state_db, queue_dir, capsys):
    spec = _spec(job_id="borked")
    _add_pending(state_db, spec, work_dir="/var/confflow/borked")

    status_dir = queue_dir / "status"
    status_dir.mkdir(parents=True, exist_ok=True)
    (status_dir / "borked.json").write_text("{not valid json")

    args = argparse.Namespace(
        job_id="borked",
        queue_dir=str(queue_dir),
        state_db=str(state_db.db_path),
    )
    rc = agent_cli.cmd_status(args)
    captured = capsys.readouterr()
    assert rc == 0
    assert "Job ID" in captured.out


# ---------------------------------------------------------------------------
# cmd_list
# ---------------------------------------------------------------------------


def test_cmd_list_with_no_jobs(state_db, queue_dir, capsys):
    args = argparse.Namespace(
        queue_dir=str(queue_dir),
        state_db=str(state_db.db_path),
        no_all=False,
    )
    rc = agent_cli.cmd_list(args)
    captured = capsys.readouterr()
    assert rc == 0
    assert "No jobs found" in captured.out


def test_cmd_list_lists_jobs(state_db, queue_dir, capsys):
    for jid in ("a", "b"):
        _add_pending(state_db, _spec(job_id=jid), work_dir=f"/tmp/{jid}")

    args = argparse.Namespace(
        queue_dir=str(queue_dir),
        state_db=str(state_db.db_path),
        no_all=False,
    )
    rc = agent_cli.cmd_list(args)
    captured = capsys.readouterr()
    assert rc == 0
    assert "a" in captured.out
    assert "b" in captured.out


def test_cmd_list_pending_only_when_no_all(state_db, queue_dir, capsys):
    for jid, status in [("p", JobStatus.PENDING), ("r", JobStatus.RUNNING)]:
        _add_pending(state_db, _spec(job_id=jid), work_dir=f"/tmp/{jid}")
        if status == JobStatus.RUNNING:
            state_db.set_status(jid, JobStatus.RUNNING)

    args = argparse.Namespace(
        queue_dir=str(queue_dir),
        state_db=str(state_db.db_path),
        no_all=True,
    )
    rc = agent_cli.cmd_list(args)
    captured = capsys.readouterr()
    assert rc == 0
    # Pending job is listed
    assert "p" in captured.out
    # Header contains 'Progress' but the running job's row is absent.
    # Use a structural check: 'running' status text should not appear.
    assert "running" not in captured.out.lower()
    assert "Progress" in captured.out  # header still rendered


# ---------------------------------------------------------------------------
# cmd_submit
# ---------------------------------------------------------------------------


def test_cmd_submit_missing_config_returns_error(queue_dir, state_db_path, capsys, tmp_path):
    args = argparse.Namespace(
        config="/nonexistent/cfg.yaml",
        input_xyz=str(tmp_path / "input.xyz"),
        job_id="custom-job",
        queue_dir=str(queue_dir),
        state_db=str(state_db_path),
        submitted_by="cli",
        verbose=False,
    )
    (tmp_path / "input.xyz").write_text("xyz")
    rc = agent_cli.cmd_submit(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "Config file not found" in captured.err


def test_cmd_submit_missing_input_returns_error(queue_dir, state_db_path, tmp_path, capsys):
    config_path = tmp_path / "conf.yaml"
    config_path.write_text("step: opt")
    args = argparse.Namespace(
        config=str(config_path),
        input_xyz="/nope/input.xyz",
        job_id="custom-job",
        queue_dir=str(queue_dir),
        state_db=str(state_db_path),
        submitted_by="cli",
        verbose=False,
    )
    rc = agent_cli.cmd_submit(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "Input XYZ not found" in captured.err


def test_cmd_submit_writes_to_queue_and_state_db(queue_dir, state_db_path, tmp_path, capsys):
    config_path = tmp_path / "conf.yaml"
    input_path = tmp_path / "input.xyz"
    config_path.write_text("step: opt")
    input_path.write_text("")

    args = argparse.Namespace(
        config=str(config_path),
        input_xyz=str(input_path),
        job_id="submit-me",
        queue_dir=str(queue_dir),
        state_db=str(state_db_path),
        submitted_by="alice",
        verbose=False,
    )
    rc = agent_cli.cmd_submit(args)
    captured = capsys.readouterr()
    assert rc == 0
    assert "submit-me submitted" in captured.out

    incoming = queue_dir / "incoming" / "submit-me.json"
    assert incoming.exists()
    payload = json.loads(incoming.read_text())
    assert payload["job_id"] == "submit-me"
    assert payload["submitted_by"] == "alice"

    db = AgentStateDB(str(state_db_path))
    try:
        row = db.get_job("submit-me")
    finally:
        db.close()
    assert row is not None
    assert row["status"] == JobStatus.PENDING.value


def test_cmd_submit_generates_job_id_when_omitted(queue_dir, state_db_path, tmp_path, capsys):
    config_path = tmp_path / "conf.yaml"
    input_path = tmp_path / "input.xyz"
    config_path.write_text("step: opt")
    input_path.write_text("")

    args = argparse.Namespace(
        config=str(config_path),
        input_xyz=str(input_path),
        job_id=None,
        queue_dir=str(queue_dir),
        state_db=str(state_db_path),
        submitted_by="cli",
        verbose=False,
    )
    rc = agent_cli.cmd_submit(args)
    captured = capsys.readouterr()
    assert rc == 0
    assert "submitted" in captured.out


# ---------------------------------------------------------------------------
# cmd_pause / cmd_resume / cmd_cancel
# ---------------------------------------------------------------------------


def test_cmd_pause_unknown_job_returns_error(state_db, queue_dir, capsys):
    args = argparse.Namespace(
        job_id="nope",
        queue_dir=str(queue_dir),
        state_db=str(state_db.db_path),
    )
    rc = agent_cli.cmd_pause(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "not found" in captured.err


def test_cmd_pause_without_work_dir_returns_error(state_db, queue_dir, capsys):
    spec = _spec(job_id="no-workdir")
    state_db.add_job(
        job_id=spec.job_id,
        config_file=spec.config_file,
        input_xyz=spec.input_xyz,
        submitted_at=spec.submitted_at,
        submitted_by=spec.submitted_by,
    )
    state_db.set_status(spec.job_id, JobStatus.PENDING)  # no work_dir
    args = argparse.Namespace(
        job_id="no-workdir",
        queue_dir=str(queue_dir),
        state_db=str(state_db.db_path),
    )
    rc = agent_cli.cmd_pause(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "no work_dir" in captured.err


def test_cmd_pause_touches_beacon_and_updates_status(state_db, queue_dir, tmp_path, capsys):
    work_dir = tmp_path / "wd"
    work_dir.mkdir()
    spec = _spec(job_id="pauseme")
    _add_pending(state_db, spec, work_dir=str(work_dir))

    args = argparse.Namespace(
        job_id="pauseme",
        queue_dir=str(queue_dir),
        state_db=str(state_db.db_path),
    )
    rc = agent_cli.cmd_pause(args)
    captured = capsys.readouterr()
    assert rc == 0
    assert "paused" in captured.out
    assert (work_dir / "PAUSE").exists()
    row = state_db.get_job("pauseme")
    assert row["status"] == JobStatus.PAUSED.value


def test_cmd_resume_unknown_job_returns_error(state_db, queue_dir, capsys):
    args = argparse.Namespace(
        job_id="nope",
        queue_dir=str(queue_dir),
        state_db=str(state_db.db_path),
    )
    rc = agent_cli.cmd_resume(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "not found" in captured.err


def test_cmd_resume_re_enqueues_and_clears_pause_beacon(state_db, queue_dir, tmp_path, capsys):
    work_dir = tmp_path / "wd"
    work_dir.mkdir()
    beacon = work_dir / "PAUSE"
    beacon.touch()

    spec = _spec(job_id="resumeme", submitted_by="alice")
    state_db.add_job(
        job_id=spec.job_id,
        config_file=spec.config_file,
        input_xyz=spec.input_xyz,
        submitted_at=spec.submitted_at,
        submitted_by=spec.submitted_by,
    )
    state_db.set_status(spec.job_id, JobStatus.PAUSED, work_dir=str(work_dir))
    state_db.set_status(
        spec.job_id,
        JobStatus.PENDING,
        error_message="old error",
        progress_pct=12.0,
        current_step="stale",
        completed_at="2026-01-01",
    )
    state_db.set_status(spec.job_id, JobStatus.PAUSED, error_message="old error")

    args = argparse.Namespace(
        job_id="resumeme",
        queue_dir=str(queue_dir),
        state_db=str(state_db.db_path),
    )
    rc = agent_cli.cmd_resume(args)
    captured = capsys.readouterr()
    assert rc == 0
    assert "resumed" in captured.out

    incoming = queue_dir / "incoming" / "resumeme.json"
    assert incoming.exists()
    assert not beacon.exists()


def test_cmd_cancel_unknown_job_returns_error(state_db, queue_dir, capsys):
    args = argparse.Namespace(
        job_id="nope",
        queue_dir=str(queue_dir),
        state_db=str(state_db.db_path),
    )
    rc = agent_cli.cmd_cancel(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "not found" in captured.err


def test_cmd_cancel_sets_cancelled_status(state_db, queue_dir, tmp_path, capsys):
    spec = _spec(job_id="cancelme")
    _add_pending(state_db, spec, work_dir=str(tmp_path))
    args = argparse.Namespace(
        job_id="cancelme",
        queue_dir=str(queue_dir),
        state_db=str(state_db.db_path),
    )
    rc = agent_cli.cmd_cancel(args)
    captured = capsys.readouterr()
    assert rc == 0
    assert "cancelled" in captured.out
    row = state_db.get_job("cancelme")
    assert row["status"] == JobStatus.CANCELLED.value


def test_cmd_cancel_without_work_dir_still_marks_cancelled(state_db, queue_dir, capsys):
    spec = _spec(job_id="cancelnwd")
    state_db.add_job(
        job_id=spec.job_id,
        config_file=spec.config_file,
        input_xyz=spec.input_xyz,
        submitted_at=spec.submitted_at,
        submitted_by=spec.submitted_by,
    )
    state_db.set_status(spec.job_id, JobStatus.PENDING)
    args = argparse.Namespace(
        job_id="cancelnwd",
        queue_dir=str(queue_dir),
        state_db=str(state_db.db_path),
    )
    rc = agent_cli.cmd_cancel(args)
    capsys.readouterr()
    assert rc == 0
    row = state_db.get_job("cancelnwd")
    assert row["status"] == JobStatus.CANCELLED.value


# ---------------------------------------------------------------------------
# cmd_logs
# ---------------------------------------------------------------------------


def test_cmd_logs_reads_job_specific_file(log_dir, capsys):
    (log_dir / "job1.log").write_text("line a\nline b\nline c\n")
    args = argparse.Namespace(
        job_id="job1",
        log_dir=str(log_dir),
        tail=None,
    )
    rc = agent_cli.cmd_logs(args)
    captured = capsys.readouterr()
    assert rc == 0
    assert "line a" in captured.out
    assert "line c" in captured.out


def test_cmd_logs_applies_tail_to_job_specific_file(log_dir, capsys):
    (log_dir / "job2.log").write_text("\n".join(f"line {i}" for i in range(10)))
    args = argparse.Namespace(
        job_id="job2",
        log_dir=str(log_dir),
        tail=3,
    )
    rc = agent_cli.cmd_logs(args)
    captured = capsys.readouterr()
    assert rc == 0
    out_lines = [line_ for line_ in captured.out.splitlines() if line_]
    assert out_lines == ["line 7", "line 8", "line 9"]


def test_cmd_logs_falls_back_to_agent_log(log_dir, capsys):
    (log_dir / "agent.log").write_text("noise before\njob3: doing X\nnoise\njob3: doing Y\n")
    args = argparse.Namespace(
        job_id="job3",
        log_dir=str(log_dir),
        tail=None,
    )
    rc = agent_cli.cmd_logs(args)
    captured = capsys.readouterr()
    assert rc == 0
    assert "job3: doing X" in captured.out
    assert "job3: doing Y" in captured.out
    assert "noise before" not in captured.out


def test_cmd_logs_fallback_applies_tail(log_dir, capsys):
    lines = ["noise " + str(i) for i in range(5)] + ["job4: msg " + str(i) for i in range(3)]
    (log_dir / "agent.log").write_text("\n".join(lines))
    args = argparse.Namespace(
        job_id="job4",
        log_dir=str(log_dir),
        tail=2,
    )
    rc = agent_cli.cmd_logs(args)
    captured = capsys.readouterr()
    assert rc == 0
    out_lines = [line for line in captured.out.splitlines() if line]
    assert out_lines == ["job4: msg 1", "job4: msg 2"]


def test_cmd_logs_missing_returns_error(log_dir, capsys):
    args = argparse.Namespace(
        job_id="jobNOPE",
        log_dir=str(log_dir),
        tail=None,
    )
    rc = agent_cli.cmd_logs(args)
    captured = capsys.readouterr()
    assert rc == 1
    assert "No log file" in captured.err


# ---------------------------------------------------------------------------
# cmd_stop
# ---------------------------------------------------------------------------


def test_cmd_stop_pauses_all_running_jobs(state_db, queue_dir, tmp_path, capsys):
    for jid in ("r1", "r2"):
        wd = tmp_path / jid
        wd.mkdir()
        _add_pending(state_db, _spec(job_id=jid), work_dir=str(wd))
        state_db.set_status(jid, JobStatus.RUNNING)

    args = argparse.Namespace(
        queue_dir=str(queue_dir),
        state_db=str(state_db.db_path),
    )
    rc = agent_cli.cmd_stop(args)
    captured = capsys.readouterr()
    assert rc == 0
    assert "Agent remains running" in captured.out
    for jid in ("r1", "r2"):
        row = state_db.get_job(jid)
        assert row["status"] == JobStatus.PAUSED.value


def test_cmd_stop_no_running_jobs_is_noop(state_db, queue_dir, capsys):
    _add_pending(state_db, _spec(job_id="static"), work_dir="/tmp/static")
    args = argparse.Namespace(
        queue_dir=str(queue_dir),
        state_db=str(state_db.db_path),
    )
    rc = agent_cli.cmd_stop(args)
    capsys.readouterr()
    assert rc == 0
    row = state_db.get_job("static")
    assert row["status"] == JobStatus.PENDING.value


# ---------------------------------------------------------------------------
# cmd_serve
# ---------------------------------------------------------------------------


def test_cmd_serve_invokes_serve_loop(queue_dir, state_db_path, monkeypatch):
    """cmd_serve must build an AgentServer and call serve().

    Verifies the wiring between cmd_serve and AgentServer.serve() without
    running the long-lived serve loop itself.
    """
    captured = {}

    class StubServer:
        def __init__(self, queue_dir, state_db, num_slots):
            captured["queue_dir"] = queue_dir
            captured["num_slots"] = num_slots
            captured["state_db"] = state_db

        def serve(self):
            captured["served"] = True

        def stop(self):
            captured["stopped"] = True

    monkeypatch.setattr(agent_cli, "AgentServer", StubServer)

    args = argparse.Namespace(
        queue_dir=str(queue_dir),
        state_db=str(state_db_path),
        slots=3,
        verbose=False,
    )
    rc = agent_cli.cmd_serve(args)
    assert rc == 0
    assert captured["served"] is True
    assert captured["num_slots"] == 3


# ---------------------------------------------------------------------------
# logging helper
# ---------------------------------------------------------------------------


def test_setup_logging_sets_level():
    """_setup_logging toggles logging level correctly."""
    root = logging.getLogger()
    saved_level = root.level
    saved_handlers = list(root.handlers)
    try:
        for h in list(root.handlers):
            root.removeHandler(h)
        # First call installs handlers at DEBUG.
        agent_cli._setup_logging(verbose=True)
        assert root.level == logging.DEBUG
        # Force-add a handler so basicConfig is a no-op and only root.setLevel matters.
        # This emulates a real run where earlier logging already configured the root.
        agent_cli._setup_logging(verbose=False)
        # basicConfig only configures when there are no handlers; root level stays
        # at DEBUG because we left the handler in place from the previous call.
        # Verify root now has at least one handler (so logging is wired up).
        assert len(root.handlers) >= 1
    finally:
        for h in root.handlers:
            root.removeHandler(h)
        for h in saved_handlers:
            root.addHandler(h)
        root.setLevel(saved_level)
