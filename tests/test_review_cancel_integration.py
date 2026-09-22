"""Cancellation must respect process ownership, including a shared CWD."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from confflow.calc.executor import LocalCalcExecutor


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-session isolation")
def test_cancel_does_not_claim_independent_process_in_same_directory(tmp_path: Path) -> None:
    command = [sys.executable, "-c", "import time; time.sleep(60)"]
    independent = subprocess.Popen(command, cwd=tmp_path, start_new_session=True)
    executor = LocalCalcExecutor()
    handle = None
    try:
        handle = executor.submit(
            str(tmp_path), "job", SimpleNamespace(log_ext="log"), [], {}, command, None
        )
        executor.cancel(handle)
        assert independent.poll() is None
        assert executor.is_terminal(handle)
    finally:
        processes = [independent]
        if handle is not None:
            processes.append(handle.executor_data["_proc"])
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        if handle is not None:
            executor._close_streams(handle)
