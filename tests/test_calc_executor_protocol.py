"""Phase 1 plumbing tests for the CalcExecutor protocol."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _policy(*, input_ext: str = "inp", log_ext: str = "log", name: str = "Mock"):
    policy = MagicMock()
    policy.name = name
    policy.input_ext = input_ext
    policy.log_ext = log_ext
    return policy


class _MockHandle:
    def __init__(self, job_name: str, work_dir: str, payload: dict) -> None:
        self.job_name = job_name
        self.work_dir = work_dir
        self.submitted_at = 0.0
        self.executor_data = payload


class _MockStatus:
    def __init__(self, is_terminal: bool, succeeded: bool) -> None:
        self.is_terminal = is_terminal
        self.succeeded = succeeded
        self.exit_code = 0 if succeeded else 1
        self.error = None if succeeded else "mock failure"


class MockCalcExecutor:
    """Records submit/poll sequences without launching processes."""

    def __init__(self) -> None:
        self.submitted: list[dict] = []
        self.polled: list[str] = []
        self.handles: dict[str, dict] = {}
        self._terminal = {"job_b": True}
        self._succeeded = {"job_b": True}

    def submit(self, work_dir, job_name, policy, coords, config, cmd, env):
        self.submitted.append(
            {
                "work_dir": work_dir,
                "job_name": job_name,
                "cmd": list(cmd),
                "env": dict(env or {}),
            }
        )
        payload: dict = {"policy": policy, "config": config}
        self.handles[job_name] = payload
        return _MockHandle(job_name=job_name, work_dir=work_dir, payload=payload)

    def is_terminal(self, handle):
        self.polled.append(handle.job_name)
        return self._terminal.get(handle.job_name, False)

    def succeeded(self, handle):
        return self._succeeded.get(handle.job_name, False)

    def error(self, handle):
        return None if self.succeeded(handle) else "mock failure"

    def cancel(self, handle):
        return None

    def fetch_output(self, handle, log, config, is_sp_task=False):
        handle.executor_data["fetched"] = (log, is_sp_task)
        return {"mock": True, "job": handle.job_name}

    def poll(self, handle):
        return _MockStatus(
            is_terminal=self.is_terminal(handle),
            succeeded=self.succeeded(handle),
        )


def test_mock_executor_records_submit_and_poll(tmp_path):
    executor = MockCalcExecutor()
    handle = executor.submit(
        work_dir=str(tmp_path),
        job_name="job_a",
        policy=_policy(),
        coords=None,
        config={},
        cmd=["g16", "job_a.gjf"],
        env={"FOO": "bar"},
    )
    assert handle.job_name == "job_a"
    assert executor.submitted[0]["cmd"] == ["g16", "job_a.gjf"]
    assert executor.submitted[0]["env"] == {"FOO": "bar"}
    status = executor.poll(handle)
    assert status.is_terminal is False
    # poll() delegates to is_terminal once; cancel/success probes stay separate
    assert executor.polled == ["job_a"]


def test_mock_executor_marks_terminal_via_polled_state(tmp_path):
    executor = MockCalcExecutor()
    handle_a = executor.submit(str(tmp_path), "job_a", _policy(), None, {}, ["true"], None)
    handle_b = executor.submit(str(tmp_path), "job_b", _policy(), None, {}, ["true"], None)
    assert executor.poll(handle_a).is_terminal is False
    status_b = executor.poll(handle_b)
    assert status_b.is_terminal is True
    assert status_b.succeeded is True


def test_protocol_passthrough_through_calculation_step(tmp_path):
    """A supplied executor owns launch, polling, and output parsing."""
    from confflow.calc.components.executor import _run_calculation_step

    policy = _policy()
    policy.check_termination.return_value = True
    mock = MockCalcExecutor()
    mock._terminal["job"] = True
    mock._succeeded["job"] = True

    result = _run_calculation_step(
        str(tmp_path),
        "job",
        policy,
        ["H 0 0 0"],
        {"stop_check_interval_seconds": 0.01},
        calc_executor=mock,
    )

    assert result == {"mock": True, "job": "job"}
    assert mock.submitted[0]["job_name"] == "job"
    assert mock.polled == ["job"]
    assert mock.handles["job"]["fetched"][1] is False


def test_protocol_default_uses_local_executor(tmp_path, monkeypatch):
    """Omitting the Protocol preserves the local executor fallback."""
    from confflow.calc.components import executor as component_executor

    policy = _policy()
    policy.check_termination.return_value = True
    mock = MockCalcExecutor()
    mock._terminal["job"] = True
    mock._succeeded["job"] = True
    monkeypatch.setattr(component_executor, "LocalCalcExecutor", lambda: mock)

    result = component_executor._run_calculation_step(
        str(tmp_path),
        "job",
        policy,
        ["H 0 0 0"],
        {"stop_check_interval_seconds": 0.01},
    )

    assert result == {"mock": True, "job": "job"}
    assert mock.submitted[0]["job_name"] == "job"


def test_local_executor_popen(tmp_path):
    from confflow.calc.executor import LocalCalcExecutor

    exe = LocalCalcExecutor()
    policy = _policy()
    policy.parse_output.return_value = {"energy": -1.0}
    cmd = [sys.executable, "-c", "print('hi')"]
    handle = exe.submit(str(tmp_path), "ping", policy, None, {}, cmd, None)
    proc = handle.executor_data["_proc"]
    assert isinstance(proc, subprocess.Popen)
    rc = proc.wait(timeout=10)
    assert rc == 0
    assert exe.is_terminal(handle) is True
    assert exe.succeeded(handle) is True
    assert exe.error(handle) is None
    out = exe.fetch_output(handle, str(tmp_path / "ping.log"), {"iprog": 1}, is_sp_task=True)
    assert isinstance(out, dict)


def test_local_executor_failure_path(tmp_path):
    from confflow.calc.executor import LocalCalcExecutor

    exe = LocalCalcExecutor()
    cmd = [sys.executable, "-c", "import sys; sys.exit(3)"]
    handle = exe.submit(str(tmp_path), "fail", _policy(), None, {}, cmd, None)
    proc = handle.executor_data["_proc"]
    assert proc.wait(timeout=10) == 3
    assert exe.is_terminal(handle) is True
    assert exe.succeeded(handle) is False
    assert exe.error(handle) == "subprocess exited with code 3"


def test_windows_threadpool_selection(monkeypatch):
    """The production runner chooses a thread pool on Windows."""
    from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

    from confflow.calc import runner

    monkeypatch.setattr(runner.sys, "platform", "win32")

    assert runner._task_pool_type() is ThreadPoolExecutor
    assert runner._task_pool_type() is not ProcessPoolExecutor


def test_task_runner_preserves_legacy_monkeypatch_signature(tmp_path, monkeypatch):
    """No custom executor means existing patched callables receive no new kwargs."""
    from confflow.calc.components import executor as component_executor
    from confflow.calc.components.task_runner import TaskRunner

    def legacy_run(work_dir, job_name, policy, coords, config, is_sp_task=False):
        del work_dir, job_name, policy, config, is_sp_task
        return {"final_coords": coords, "e_low": -1.0}

    monkeypatch.setattr(component_executor, "_run_calculation_step", legacy_run)
    monkeypatch.setattr(component_executor, "handle_backups", lambda *args, **kwargs: True)
    result = TaskRunner().run(
        {
            "job_name": "job",
            "work_dir": str(tmp_path / "work"),
            "config": {"itask": 1, "iprog": 1},
            "coords": ["H 0 0 0"],
        }
    )

    assert result["status"] == "success"


def test_task_runner_forwards_custom_executor(tmp_path, monkeypatch):
    """TaskRunner forwards a caller-supplied executor to the calc step."""
    from confflow.calc.components import executor as component_executor
    from confflow.calc.components.task_runner import TaskRunner

    supplied = MockCalcExecutor()
    seen: dict[str, object] = {}

    def custom_run(work_dir, job_name, policy, coords, config, is_sp_task=False, *, calc_executor):
        del work_dir, job_name, policy, config, is_sp_task
        seen["executor"] = calc_executor
        return {"final_coords": coords, "e_low": -1.0}

    monkeypatch.setattr(component_executor, "_run_calculation_step", custom_run)
    monkeypatch.setattr(component_executor, "handle_backups", lambda *args, **kwargs: True)
    result = TaskRunner(calc_executor=supplied).run(
        {
            "job_name": "job",
            "work_dir": str(tmp_path / "work"),
            "config": {"itask": 1, "iprog": 1},
            "coords": ["H 0 0 0"],
        }
    )

    assert result["status"] == "success"
    assert seen["executor"] is supplied


def test_local_executor_thread_safe_handle(tmp_path):
    """LocalCalcExecutor must allow concurrent polls from different threads."""
    from confflow.calc.executor import LocalCalcExecutor

    exe = LocalCalcExecutor()
    cmd = [sys.executable, "-c", "import time; time.sleep(0.2)"]
    handle = exe.submit(str(tmp_path), "share", _policy(), None, {}, cmd, None)
    results: list[bool] = []
    lock = threading.Lock()

    def poll_until_terminal() -> None:
        while not exe.is_terminal(handle):
            pass
        with lock:
            results.append(exe.succeeded(handle))

    threads = [threading.Thread(target=poll_until_terminal) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert len(results) == 4
    assert all(results)


@pytest.mark.skipif(os.name != "posix", reason="process-group contract requires POSIX")
def test_local_executor_cancel_stops_term_ignoring_child(tmp_path):
    """Cancellation escalates from TERM to KILL for the whole calc group."""
    from confflow.calc.executor import LocalCalcExecutor

    work_dir = tmp_path / "calc"
    work_dir.mkdir()
    child_pid_file = work_dir / "child.pid"
    parent_code = (
        "import pathlib, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'], "
        "cwd=sys.argv[1]); "
        "pathlib.Path(sys.argv[2]).write_text(str(child.pid), encoding='ascii'); "
        "time.sleep(60)"
    )
    executor = LocalCalcExecutor(terminate_timeout=0.25, kill_timeout=1.0)
    handle = executor.submit(
        str(work_dir),
        "tree",
        _policy(),
        None,
        {},
        [sys.executable, "-c", parent_code, str(work_dir), str(child_pid_file)],
        None,
    )
    process = handle.executor_data["_proc"]
    child_pid: int | None = None
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not child_pid_file.exists():
            time.sleep(0.01)
        assert child_pid_file.exists()
        child_pid = int(child_pid_file.read_text(encoding="ascii"))
        assert process.poll() is None

        executor.cancel(handle)

        assert process.poll() is not None
        assert not _pid_is_running(child_pid)
        assert handle.executor_data["_cancel_confirmed"] is True
        assert "_stdout" not in handle.executor_data
        assert "_stderr" not in handle.executor_data
    finally:
        _kill_test_process_group(process, child_pid)


@pytest.mark.skipif(os.name != "posix", reason="process-group contract requires POSIX")
@pytest.mark.parametrize("boundary_lookup_missing", [False, True])
def test_local_executor_cancel_handles_exited_parent_with_live_child(
    tmp_path, monkeypatch, boundary_lookup_missing
):
    """A child left behind after its parent exits remains in the calc boundary."""
    from confflow.calc.executor import LocalCalcExecutor

    if boundary_lookup_missing:
        monkeypatch.setattr("confflow.calc.executor._safe_process_group_id", lambda pid: None)
        monkeypatch.setattr("confflow.calc.executor._safe_session_id", lambda pid: None)

    work_dir = tmp_path / "calc"
    work_dir.mkdir()
    child_pid_file = work_dir / "child.pid"
    parent_code = (
        "import pathlib, subprocess, sys; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
        "cwd=sys.argv[1]); "
        "pathlib.Path(sys.argv[2]).write_text(str(child.pid), encoding='ascii')"
    )
    executor = LocalCalcExecutor(terminate_timeout=0.25, kill_timeout=1.0)
    handle = executor.submit(
        str(work_dir),
        "orphan",
        _policy(),
        None,
        {},
        [sys.executable, "-c", parent_code, str(work_dir), str(child_pid_file)],
        None,
    )
    process = handle.executor_data["_proc"]
    child_pid: int | None = None
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not child_pid_file.exists():
            time.sleep(0.01)
        assert child_pid_file.exists()
        child_pid = int(child_pid_file.read_text(encoding="ascii"))
        assert process.wait(timeout=5) == 0
        assert _pid_is_running(child_pid)
        # Process enumeration is a snapshot: one member can disappear before
        # the following getpgid/getsid calls without aborting cancellation.
        import psutil

        original_process_iter = psutil.process_iter

        def iter_with_disappeared_process(attrs):
            yield SimpleNamespace(pid=2**30, info={"status": "running"})
            yield from original_process_iter(attrs)

        monkeypatch.setattr(psutil, "process_iter", iter_with_disappeared_process)
        assert executor.is_terminal(handle) is False

        executor.cancel(handle)

        assert not _pid_is_running(child_pid)
        assert handle.executor_data["_cancel_confirmed"] is True
    finally:
        _kill_test_process_group(process, child_pid)


@pytest.mark.skipif(os.name != "posix", reason="process-group contract requires POSIX")
def test_local_executor_cancel_does_not_kill_unrelated_process(tmp_path):
    """The dedicated calculation group does not include another task."""
    from confflow.calc.executor import LocalCalcExecutor

    calc_dir = tmp_path / "calc"
    other_dir = tmp_path / "other"
    calc_dir.mkdir()
    other_dir.mkdir()
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=other_dir,
    )
    executor = LocalCalcExecutor(terminate_timeout=0.25, kill_timeout=1.0)
    handle = executor.submit(
        str(calc_dir),
        "isolated",
        _policy(),
        None,
        {},
        [sys.executable, "-c", "import time; time.sleep(60)"],
        None,
    )
    process = handle.executor_data["_proc"]
    try:
        executor.cancel(handle)
        assert process.poll() is not None
        assert unrelated.poll() is None
    finally:
        _kill_test_process_group(process, None)
        if unrelated.poll() is None:
            unrelated.kill()
        unrelated.wait(timeout=5)


def _pid_is_running(pid: int) -> bool:
    import psutil

    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False


def _kill_test_process_group(process: subprocess.Popen, child_pid: int | None) -> None:
    import os
    import signal

    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
    if child_pid is not None and _pid_is_running(child_pid):
        try:
            os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


@pytest.mark.skipif(os.name != "posix", reason="process-group contract requires POSIX")
def test_group_signal_error_is_an_unconfirmed_cancellation(monkeypatch, tmp_path):
    from confflow.calc.executor import CalcCancellationError, CalcHandle, LocalCalcExecutor

    executor = LocalCalcExecutor(terminate_timeout=0, kill_timeout=0)
    handle = CalcHandle("task", str(tmp_path), 0, {"_proc": MagicMock(), "_pgid": 12345})
    monkeypatch.setattr(executor, "_refresh_process_boundary", lambda handle: None)
    monkeypatch.setattr(executor, "_group_is_safe_to_signal", lambda handle: True)

    def fail_signal(pgid, sig):
        raise OSError("group signal failed")

    monkeypatch.setattr(os, "killpg", fail_signal)
    with pytest.raises(CalcCancellationError, match="failed to signal") as raised:
        executor.cancel(handle)
    assert raised.value.confirmed is False
    assert handle.executor_data.get("_cancel_confirmed") is not True


@pytest.mark.skipif(os.name != "posix", reason="process-group contract requires POSIX")
def test_missing_psutil_cannot_claim_orphaned_group_stopped(monkeypatch, tmp_path):
    from confflow.calc.executor import CalcHandle, LocalCalcExecutor

    executor = LocalCalcExecutor()
    handle = CalcHandle("task", str(tmp_path), 0, {"_pgid": 12345})
    monkeypatch.setattr("confflow.calc.executor._psutil", None)
    monkeypatch.setattr(executor, "_group_is_safe_to_signal", lambda handle: False)
    probes = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: probes.append((pgid, sig)))
    assert executor._live_boundary_processes(handle, root_reaped=True)
    assert probes == [(12345, 0)]
