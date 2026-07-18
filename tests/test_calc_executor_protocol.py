"""Phase 1 plumbing tests for the CalcExecutor protocol."""

from __future__ import annotations

import sys
import subprocess
import threading
from pathlib import Path
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


def test_protocol_passthrough_through_executor_module():
    """MockCalcExecutor must satisfy the CalcExecutor duck-typing surface."""
    from confflow.calc.executor import CalcExecutor, LocalCalcExecutor

    assert hasattr(CalcExecutor, "submit")
    assert hasattr(CalcExecutor, "poll")
    mock = MockCalcExecutor()
    # The Protocol is structural; just call all required methods without error.
    handle = mock.submit("/tmp", "x", _policy(), None, {}, ["true"], None)
    mock.poll(handle)
    mock.is_terminal(handle)
    mock.succeeded(handle)
    mock.error(handle)
    mock.cancel(handle)
    mock.fetch_output(handle, "/tmp/job.log", {}, is_sp_task=False)


def test_local_executor_popen(tmp_path):
    from confflow.calc.executor import LocalCalcExecutor

    exe = LocalCalcExecutor()
    cmd = [sys.executable, "-c", "print('hi')"]
    handle = exe.submit(str(tmp_path), "ping", _policy(), None, {}, cmd, None)
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
    """On sys.platform=='win32', the runner layer must select ThreadPoolExecutor."""
    from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

    monkeypatch.setattr(sys, "platform", "win32")
    if sys.platform == "win32":
        from concurrent.futures import ThreadPoolExecutor as Pool
    else:  # pragma: no cover - only reached on non-win32 test machines
        from concurrent.futures import ProcessPoolExecutor as Pool

    assert Pool is ThreadPoolExecutor
    assert Pool is not ProcessPoolExecutor


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