#!/usr/bin/env python3
"""A running lease stays held while any member of its process group lives."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

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
