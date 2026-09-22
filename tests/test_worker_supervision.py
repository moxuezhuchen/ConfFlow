"""Tests for the extracted worker stop-proof helpers."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import confflow.control_worker as control_worker
import confflow.worker_supervision as worker_supervision
from confflow.calc.executor import LocalCalcExecutor

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="worker supervision contract requires POSIX"
)


def test_supervision_module_validates_only_complete_owner_markers() -> None:
    assert worker_supervision._complete_owner_marker(
        {"pid": 42, "pgid": 42, "isolated_session": True}
    )
    for owner in (
        None,
        {},
        {"pid": 42, "pgid": 42},
        {"pid": 0, "pgid": 42, "isolated_session": True},
        {"pid": 42, "pgid": "42", "isolated_session": True},
    ):
        assert not worker_supervision._complete_owner_marker(owner)


def test_supervision_detects_calculation_in_its_own_session(tmp_path: Path) -> None:
    """A detached calculation must block recovery even outside the worker group."""
    work_dir = tmp_path / "work"
    task_dir = work_dir / "step" / "task"
    task_dir.mkdir(parents=True)
    executor = LocalCalcExecutor()
    handle = executor.submit(
        str(task_dir),
        "task",
        SimpleNamespace(log_ext="log"),
        [],
        {},
        [sys.executable, "-c", "import time; time.sleep(60)"],
        None,
    )
    process = handle.executor_data["_proc"]
    try:
        assert os.getsid(process.pid) == process.pid
        assert worker_supervision._has_live_work_process(str(work_dir))
        assert not worker_supervision._cancel_owner_is_stopped(str(work_dir), owner=None)
        executor.cancel(handle)
        assert not worker_supervision._has_live_work_process(str(work_dir))
        assert worker_supervision._cancel_owner_is_stopped(str(work_dir), owner=None)
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        executor._close_streams(handle)


def test_control_worker_supervision_wrappers_preserve_legacy_patch_seams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = {"pid": 42, "pgid": 42, "isolated_session": True}
    seen: list[object] = []

    def fake_complete(candidate: dict[str, object] | None) -> bool:
        seen.append(("complete", candidate))
        return candidate == owner

    def fake_live(work_dir: str, *, owner: dict[str, object] | None = None) -> bool:
        seen.append(("live", work_dir, owner))
        return False

    monkeypatch.setattr(control_worker, "_complete_owner_marker", fake_complete)
    monkeypatch.setattr(control_worker, "_has_live_work_process", fake_live)
    assert control_worker._cancel_owner_is_stopped("/attempt/work", owner=owner)
    assert seen == [
        ("complete", owner),
        ("live", "/attempt/work", owner),
    ]


def test_control_worker_wrappers_delegate_to_extracted_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = {"pid": 42, "pgid": 42, "isolated_session": True}
    monkeypatch.setattr(worker_supervision, "_complete_owner_marker", lambda value: value == owner)
    monkeypatch.setattr(
        worker_supervision,
        "_has_live_work_process",
        lambda work_dir, *, owner=None: False,
    )
    assert control_worker._complete_owner_marker(owner)
    assert not control_worker._has_live_work_process("/missing", owner=None)


def test_supervision_uses_signal_zero_and_never_terminates_a_process() -> None:
    package_root = Path(__file__).parents[1] / "confflow"
    control_source = (package_root / "control_worker.py").read_text(encoding="utf-8")
    supervision_source = (package_root / "worker_supervision.py").read_text(encoding="utf-8")
    control_tree = ast.parse(control_source)
    supervision_tree = ast.parse(supervision_source)
    names = {node.name for node in supervision_tree.body if isinstance(node, ast.FunctionDef)}
    expected = {
        "_complete_owner_marker",
        "_cancel_owner_is_stopped",
        "_has_live_work_process",
    }
    assert expected <= names
    for name in expected:
        node = next(node for node in supervision_tree.body if getattr(node, "name", None) == name)
        assert ast.unparse(node)
    for name in expected:
        node = next(node for node in control_tree.body if getattr(node, "name", None) == name)
        assert "_worker_supervision" in ast.unparse(node)
    killpg_calls = [
        node
        for node in ast.walk(supervision_tree)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "os.killpg"
    ]
    assert len(killpg_calls) == 1
    assert len(killpg_calls[0].args) == 2
    assert ast.literal_eval(killpg_calls[0].args[1]) == 0
    forbidden = {"kill", "terminate", "send_signal", "killpg"}
    other_process_mutations = [
        ast.unparse(node.func)
        for node in ast.walk(supervision_tree)
        if isinstance(node, ast.Call)
        and ast.unparse(node.func).rsplit(".", 1)[-1] in forbidden - {"killpg"}
    ]
    assert other_process_mutations == []
    assert "psutil" not in control_source
    assert "killpg" not in control_source
