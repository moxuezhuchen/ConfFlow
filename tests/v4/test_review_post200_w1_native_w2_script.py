"""Post-#200 review W1 (native) and W2 (script): the launched program is the measured one.

A real ``ServerQuota`` holds the single core while the item waits; the program (native) or the
interpreter (script) is replaced during the wait, the lease is released, and then:

* the replacement never starts and its result is never published (the item fails closed);
* the lease is not leaked;
* restoring the original file and resuming the same run root recomputes the original result.

Scientific programs are fakes; the production entry, quota wait, supervisor and resume run
unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from confflow.application.v4_run import V4RunApplication, V4RunRequest
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.quota import ServerQuota
from tests.v4 import test_arch_review_launch_identity as h
from tests.v4._builders import run_inputs, structure_set
from tests.v4._helpers import v43_coverage

GIB = 1024**3
MARK = "changed after it was resolved and measured"


@pytest.fixture(autouse=True)
def _server_quota_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """One core and 4 GiB behind a real quota whose state lives in tmp_path."""
    config = tmp_path / "server.toml"
    config.write_text("total_cores = 1\ntotal_memory = '4GB'\n", encoding="utf-8")
    state = tmp_path / "quota"
    monkeypatch.setenv("CONFFLOW_SERVER_CONFIG", str(config))
    monkeypatch.setenv("CONFFLOW_SERVER_STATE_DIR", str(state))
    return state


class _ObserveWaiting(logging.Handler):
    """Signal when the real quota reports it is waiting; never alters acquire/submit."""

    def __init__(self) -> None:
        super().__init__()
        self.waiting = threading.Event()

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage().startswith("quota waiting:"):
            self.waiting.set()


def _queued(run: Any, swap: Any, manager: ServerQuota) -> Any:
    """Hold the core, start *run*, wait for the real quota wait, swap, then release."""
    lease = manager.acquire(1, GIB, "review-holder", "holder")
    observation = _ObserveWaiting()
    logger = logging.getLogger("confflow.execution.quota")
    previous = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(observation)
    box: dict[str, Any] = {}

    def task() -> None:
        try:
            box["report"] = run()
        except BaseException as error:  # surfaced below
            box["exception"] = repr(error)

    thread = threading.Thread(target=task, daemon=True)
    try:
        thread.start()
        assert observation.waiting.wait(40), "the item never waited for the real quota"
        swap()
    finally:
        manager.release(lease)
        thread.join(40)
        logger.removeHandler(observation)
        logger.setLevel(previous)
    assert not thread.is_alive()
    assert "exception" not in box, box
    return box["report"]


def _errors(report: Any) -> list[str]:
    return [
        item.error.message
        for step in report.step_results
        for item in step.item_results
        if item.error is not None
    ]


def _leases(state: Path) -> Any:
    path = state / "server-quota.json"
    return json.loads(path.read_text(encoding="utf-8"))["leases"] if path.exists() else {}


def _lines(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


# -- W1 native ------------------------------------------------------------------------------


def _native_program(path: Path, label: str, log: Path) -> None:
    path.write_text(
        f"#!{sys.executable}\nimport os, sys\nfrom pathlib import Path\n"
        f"with Path({str(log)!r}).open('a') as f: f.write({label!r} + '\\n')\n"
        f"os.execv({sys.executable!r}, [{sys.executable!r}, "
        f"{str(v43_coverage.FAKE_ORCA)!r}, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


class _NativeCase:
    """Program ``orca`` is A (measured); B is the replacement; the mode decides how it changes."""

    def __init__(self, root: Path, mode: str) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.mode = mode
        self.log = root / "native-launches.log"
        self.a = root / "orca-A"
        self.b = root / "orca-B"
        _native_program(self.a, "A", self.log)
        _native_program(self.b, "B", self.log)
        self.exe = root / "orca"
        if mode == "symlink":
            self.exe.symlink_to(self.a)
        else:
            shutil.copyfile(self.a, self.exe)
            self.exe.chmod(0o755)

    def _point_to(self, source: Path) -> None:
        if self.mode == "inplace":
            self.exe.write_bytes(source.read_bytes())
            return
        if self.mode == "replace":
            new = self.root / "orca.new"
            shutil.copyfile(source, new)
            new.chmod(0o755)
            os.replace(new, self.exe)
            return
        new_link = self.root / "orca.link"
        os.symlink(source, new_link)
        os.replace(new_link, self.exe)

    def replace(self) -> None:
        self._point_to(self.b)

    def restore(self) -> None:
        self._point_to(self.a)

    def run(self, run_root: Path) -> Any:
        doc = v43_coverage._document()
        doc["steps"][0]["execution"]["executable"] = str(self.exe)
        doc["steps"][0]["execution"]["env"] = {"FAKE_MODE": "success_opt"}
        return V4RunApplication().run(
            V4RunRequest(
                workflow_document=doc,
                run_inputs=run_inputs(structures={"structures": structure_set("s0")}),
                run_root=str(run_root),
                supervisor=NativeProcessSupervisor(),
            )
        )


@pytest.mark.parametrize("mode", ["inplace", "replace", "symlink"])
def test_native_program_replaced_while_queued_is_refused_then_recomputed(
    tmp_path: Path, _server_quota_env: Path, mode: str
) -> None:
    case = _NativeCase(tmp_path / mode, mode)
    run_root = tmp_path / mode / "run"
    manager = ServerQuota(1, 4 * GIB, _server_quota_env)

    queued = _queued(lambda: case.run(run_root), case.replace, manager)

    assert queued.status != "completed"
    assert any(MARK in message for message in _errors(queued))
    assert _lines(case.log) == []  # neither the measured program nor the replacement started
    assert not _leases(_server_quota_env)  # the lease was released, nothing leaked

    case.restore()
    resumed = case.run(run_root)
    assert resumed.status == "completed"
    assert _lines(case.log) == ["A"]  # recomputed by the original program, not reused


def test_native_unchanged_program_completes_after_quota_wait(
    tmp_path: Path, _server_quota_env: Path
) -> None:
    case = _NativeCase(tmp_path / "control", "inplace")
    manager = ServerQuota(1, 4 * GIB, _server_quota_env)

    report = _queued(lambda: case.run(tmp_path / "control" / "run"), lambda: None, manager)

    assert report.status == "completed"
    assert _lines(case.log) == ["A"]
    assert not _leases(_server_quota_env)


# -- W2 script ------------------------------------------------------------------------------


def _wrapper(value: int) -> str:
    return (
        f"#!{sys.executable}\nimport os,sys\n"
        f'os.environ["REVIEW_LAUNCH_X"]={str(value)!r}\n'
        f"os.execv({sys.executable!r},[{sys.executable!r},*sys.argv[1:]])\n"
    )


class _ScriptCase:
    """Control PATH registers ``review-python`` in *ambient*; the step PATH prefers *step*.

    Both interpreter wrappers are byte-identical, so only the file behind the step PATH can
    change the launched content.
    """

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = root
        ambient = root / "ambient-bin"
        self.step = root / "step-bin"
        ambient.mkdir(parents=True)
        self.step.mkdir()
        self.registered = ambient / "review-python"
        self.launched = self.step / "review-python"
        for path in (self.registered, self.launched):
            path.write_text(_wrapper(1), encoding="utf-8")
            path.chmod(0o755)
        self.log = root / "script.log"
        script = root / "echo_env.py"
        script.write_text(h._ECHO_ENV_SCRIPT.format(log=str(self.log)), encoding="utf-8")
        config = root.parent / "server.toml"
        config.write_text(
            "total_cores = 1\ntotal_memory = '4GB'\n\n[scripts.echo]\n"
            f"command = {json.dumps(['review-python', str(script)])}\n",
            encoding="utf-8",
        )
        previous = os.environ.get("PATH", "")
        monkeypatch.setenv("PATH", f"{ambient}{os.pathsep}{previous}")
        self.doc = h._script_doc({"env": {"PATH": f"{self.step}{os.pathsep}{previous}"}})

    def replace_step_interpreter(self) -> None:
        new = self.step / "review-python.new"
        new.write_text(_wrapper(2), encoding="utf-8")
        new.chmod(0o755)
        os.replace(new, self.launched)

    def restore(self) -> None:
        self.launched.write_text(_wrapper(1), encoding="utf-8")
        self.launched.chmod(0o755)

    def run(self, run_root: Path) -> Any:
        return h._run_script(run_root, self.doc)


def test_script_unchanged_interpreters_with_identical_bytes_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _ScriptCase(tmp_path / "control", monkeypatch)

    report = case.run(tmp_path / "control" / "run")

    assert report.status == "completed"
    assert h._z(report) == 1.0
    assert _lines(case.log) == ["run"]


def test_script_step_interpreter_replaced_while_queued_is_refused_then_recomputed(
    tmp_path: Path, _server_quota_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _ScriptCase(tmp_path / "swap", monkeypatch)
    run_root = tmp_path / "swap" / "run"
    manager = ServerQuota(1, 4 * GIB, _server_quota_env)

    queued = _queued(lambda: case.run(run_root), case.replace_step_interpreter, manager)

    assert queued.status != "completed"
    assert not any(step.structures for step in queued.step_results)
    assert _lines(case.log) == []  # the replaced interpreter never ran the script
    assert not _leases(_server_quota_env)

    case.restore()
    resumed = case.run(run_root)
    assert resumed.status == "completed"
    assert h._z(resumed) == 1.0  # the original interpreter, never the replaced z=2
    assert _lines(case.log) == ["run"]
