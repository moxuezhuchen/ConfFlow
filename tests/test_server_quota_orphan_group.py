#!/usr/bin/env python3
"""A running lease stays held while any member of its process group lives."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from confflow.execution import quota
from confflow.execution.quota import QuotaCancelled, ServerQuota

pytestmark = pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-only")


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def test_orphan_group_member_keeps_lease(tmp_path: Path) -> None:
    quota = ServerQuota(1, 1024, tmp_path / "quota")
    first = quota.acquire(1, 1024, "run-a", "item-a")
    pid_file = tmp_path / "child.pid"
    code = (
        "import subprocess, sys, time; from pathlib import Path; "
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)']); "
        "Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(0.3)"
    )
    leader = subprocess.Popen([sys.executable, "-c", code, str(pid_file)], start_new_session=True)
    pgid = leader.pid
    try:
        quota.mark_running(first, pgid, leader.pid, psutil.Process(leader.pid).create_time())
        leader.wait(timeout=5)
        child = int(pid_file.read_text(encoding="utf-8"))
        assert psutil.Process(child).status() != psutil.STATUS_ZOMBIE

        deadline = time.monotonic() + 2.0
        with pytest.raises(QuotaCancelled):
            quota.acquire(1, 1024, "run-b", "item-b", lambda: time.monotonic() > deadline)
        assert psutil.pid_exists(child)
    finally:
        _kill_group(pgid)
        leader.wait()
        quota.release(first)


class _FakeProcess:
    def __init__(self, status: str) -> None:
        self._status = status

    def status(self) -> str:
        return self._status


def _fake_psutil(pids, status: str = "running", pids_error: Exception | None = None):
    def _pids():
        if pids_error is not None:
            raise pids_error
        return list(pids)

    return SimpleNamespace(
        pids=_pids,
        Process=lambda pid: _FakeProcess(status),
        STATUS_ZOMBIE="zombie",
    )


def _killpg_raising(exc: Exception):
    def _killpg(pgid, sig):
        raise exc

    return _killpg


def test_group_check_falls_back_to_killpg_when_pids_fails_group_gone(monkeypatch) -> None:
    monkeypatch.setattr(quota, "_psutil", _fake_psutil([], pids_error=OSError("no proc")))
    monkeypatch.setattr(quota.os, "killpg", _killpg_raising(ProcessLookupError()))
    assert quota._group_has_live_member(4242) is False


def test_group_check_falls_back_to_killpg_permission_means_alive(monkeypatch) -> None:
    monkeypatch.setattr(quota, "_psutil", _fake_psutil([], pids_error=OSError("no proc")))
    monkeypatch.setattr(quota.os, "killpg", _killpg_raising(PermissionError()))
    assert quota._group_has_live_member(4242) is True


def test_group_check_falls_back_to_killpg_success_means_alive(monkeypatch) -> None:
    calls: list[tuple[int, int]] = []

    def _killpg(pgid, sig):
        calls.append((pgid, sig))

    monkeypatch.setattr(quota, "_psutil", _fake_psutil([], pids_error=RuntimeError("boom")))
    monkeypatch.setattr(quota.os, "killpg", _killpg)
    assert quota._group_has_live_member(4242) is True
    assert calls == [(4242, 0)]


def test_group_check_skips_pid_that_vanished_during_getpgid(monkeypatch) -> None:
    def _getpgid(pid):
        raise ProcessLookupError()

    monkeypatch.setattr(quota, "_psutil", _fake_psutil([111, 222]))
    monkeypatch.setattr(quota.os, "getpgid", _getpgid)
    assert quota._group_has_live_member(4242) is False


def test_group_check_ignores_zombie_member(monkeypatch) -> None:
    monkeypatch.setattr(quota, "_psutil", _fake_psutil([333], status="zombie"))
    monkeypatch.setattr(quota.os, "getpgid", lambda pid: 4242)
    assert quota._group_has_live_member(4242) is False


def test_group_check_live_member_holds_group(monkeypatch) -> None:
    monkeypatch.setattr(quota, "_psutil", _fake_psutil([444], status="sleeping"))
    monkeypatch.setattr(quota.os, "getpgid", lambda pid: 4242)
    assert quota._group_has_live_member(4242) is True


def test_group_check_ignores_pid_in_other_group(monkeypatch) -> None:
    monkeypatch.setattr(quota, "_psutil", _fake_psutil([555], status="sleeping"))
    monkeypatch.setattr(quota.os, "getpgid", lambda pid: 9999)
    assert quota._group_has_live_member(4242) is False
