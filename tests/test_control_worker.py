"""Tests for the producer-owned queued control worker boundary."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from confflow.application.execution.launch_lease import TokenLaunchLease
from confflow.application.execution.models import ExecutableIdentity, PrepareRequest, RunState
from confflow.application.execution.workflow_adapter import measure_executable, open_control_service
from confflow.control_worker import HANDOFF_SCHEMA, _has_live_work_process, run_control_worker
from confflow.core.exceptions import StopRequestedError

pytestmark = pytest.mark.skipif(os.name != "posix", reason="control state-root contract requires POSIX")


def test_worker_token_lease_allows_one_cross_process_owner(tmp_path: Path) -> None:
    first = TokenLaunchLease(tmp_path, "worker-run", "worker-run.launch.1")
    second = TokenLaunchLease(tmp_path, "worker-run", "worker-run.launch.1")
    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()
    assert second.acquire() is True
    second.release()


def test_worker_token_lease_blocks_a_separate_process(tmp_path: Path) -> None:
    lease = TokenLaunchLease(tmp_path, "worker-run", "worker-run.launch.1")
    assert lease.acquire() is True
    child = (
        "import sys; "
        "from confflow.application.execution.launch_lease import TokenLaunchLease; "
        "lease = TokenLaunchLease(sys.argv[1], 'worker-run', 'worker-run.launch.1'); "
        "print(lease.acquire())"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", child, str(tmp_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout.rstrip().endswith("False")
    finally:
        lease.release()


def test_worker_recovery_detects_a_live_calculation_child(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=work_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert _has_live_work_process(str(work_dir)) is True
    finally:
        child.terminate()
        child.wait(timeout=10)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def test_control_worker_consumes_existing_queued_token_without_prepare(tmp_path: Path) -> None:
    config = tmp_path / "workflow.yaml"
    input_xyz = tmp_path / "methane.xyz"
    work_dir = tmp_path / "methane_work"
    config.write_text("steps: []\n", encoding="utf-8")
    input_xyz.write_text("1\nH\nH 0 0 0\n", encoding="utf-8")
    handoff = {
        "content_schema": HANDOFF_SCHEMA,
        "run_id": "worker-run",
        "workflow_config": {"path": str(config), "sha256": hashlib.sha256(config.read_bytes()).hexdigest()},
        "tasks": [
            {
                "task_id": "methane",
                "input_xyz": str(input_xyz),
                "work_dir": str(work_dir),
                "sha256": hashlib.sha256(input_xyz.read_bytes()).hexdigest(),
            }
        ],
    }
    handoff_path = tmp_path / "handoff.json"
    handoff_path.write_bytes(_canonical(handoff))
    root = tmp_path / "state"
    service = open_control_service(root, identity_executable=sys.executable)
    identity = measure_executable(sys.executable)
    service.prepare(
        PrepareRequest(
            run_id="worker-run",
            idempotency_key="worker-run",
            request_digest="a" * 64,
            workflow_config_digest=handoff["workflow_config"]["sha256"],
            input_manifest_digest=hashlib.sha256(handoff_path.read_bytes()).hexdigest(),
            expected_executable_identity=identity,
        )
    )
    assert service.execute("worker-run").state is RunState.QUEUED

    def fake_runner(**kwargs):
        Path(kwargs["work_dir"]).mkdir(parents=True, exist_ok=True)
        return {"ok": True}

    state = run_control_worker(
        state_root=root,
        run_id="worker-run",
        handoff_path=handoff_path,
        workflow_runner=fake_runner,
    )

    assert state is RunState.COMPLETED
    assert service.status("worker-run").state is RunState.COMPLETED
    assert [event.type for event in service._repository.read("worker-run").events] == [  # noqa: SLF001
        "prepared",
        "queued",
        "running",
        "completed",
    ]


def test_control_worker_rejects_tampered_handoff_digest(tmp_path: Path) -> None:
    config = tmp_path / "workflow.yaml"
    input_xyz = tmp_path / "input.xyz"
    config.write_text("steps: []\n", encoding="utf-8")
    input_xyz.write_text("1\nH\nH 0 0 0\n", encoding="utf-8")
    handoff = {
        "content_schema": HANDOFF_SCHEMA,
        "run_id": "worker-run",
        "workflow_config": {"path": str(config), "sha256": hashlib.sha256(config.read_bytes()).hexdigest()},
        "tasks": [
            {
                "task_id": "input",
                "input_xyz": str(input_xyz),
                "work_dir": str(tmp_path / "work"),
                "sha256": hashlib.sha256(input_xyz.read_bytes()).hexdigest(),
            }
        ],
    }
    handoff_path = tmp_path / "handoff.json"
    handoff_path.write_bytes(_canonical(handoff))
    root = tmp_path / "state"
    service = open_control_service(root, identity_executable=sys.executable)
    identity = measure_executable(sys.executable)
    service.prepare(
        PrepareRequest(
            run_id="worker-run",
            idempotency_key="worker-run",
            request_digest="b" * 64,
            workflow_config_digest=handoff["workflow_config"]["sha256"],
            input_manifest_digest="c" * 64,
            expected_executable_identity=ExecutableIdentity(
                sha256=identity.sha256,
                realpath=identity.realpath,
                device_inode=identity.device_inode,
            ),
        )
    )
    service.execute("worker-run")

    try:
        run_control_worker(
            state_root=root,
            run_id="worker-run",
            handoff_path=handoff_path,
            workflow_runner=lambda **_: {"ok": True},
        )
    except Exception as error:
        assert "digest" in str(error)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("tampered handoff unexpectedly executed")


def test_control_worker_rejects_handoff_paths_outside_private_attempt_root(tmp_path: Path) -> None:
    attempt_root = tmp_path / "attempt"
    # A normal owner-controlled home/state parent may be 0755; only
    # group/world write access is unsafe for the handoff boundary.
    attempt_root.mkdir(mode=0o755)
    os.chmod(attempt_root, 0o755)
    root = attempt_root / "state"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    external = tmp_path / "external"
    external.mkdir()
    config = external / "workflow.yaml"
    input_xyz = attempt_root / "input.xyz"
    config.write_text("steps: []\n", encoding="utf-8")
    input_xyz.write_text("1\nH\nH 0 0 0\n", encoding="utf-8")
    handoff = {
        "content_schema": HANDOFF_SCHEMA,
        "run_id": "worker-path-check",
        "workflow_config": {"path": str(config), "sha256": hashlib.sha256(config.read_bytes()).hexdigest()},
        "tasks": [
            {
                "task_id": "input",
                "input_xyz": str(input_xyz),
                "work_dir": str(attempt_root / "work"),
                "sha256": hashlib.sha256(input_xyz.read_bytes()).hexdigest(),
            }
        ],
    }
    handoff_path = attempt_root / "handoff.json"
    handoff_path.write_bytes(_canonical(handoff))

    with pytest.raises(ValueError, match="attempt root"):
        run_control_worker(
            state_root=root,
            run_id="worker-path-check",
            handoff_path=handoff_path,
            workflow_runner=lambda **_: {"ok": True},
        )


def test_control_worker_rejects_symlinked_handoff_locator(tmp_path: Path) -> None:
    attempt_root = tmp_path / "attempt"
    attempt_root.mkdir(mode=0o700)
    os.chmod(attempt_root, 0o700)
    root = attempt_root / "state"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    config = attempt_root / "workflow.yaml"
    outside_input = tmp_path / "outside.xyz"
    config.write_text("steps: []\n", encoding="utf-8")
    outside_input.write_text("1\nH\nH 0 0 0\n", encoding="utf-8")
    input_link = attempt_root / "input.xyz"
    input_link.symlink_to(outside_input)
    handoff = {
        "content_schema": HANDOFF_SCHEMA,
        "run_id": "worker-symlink-check",
        "workflow_config": {"path": str(config), "sha256": hashlib.sha256(config.read_bytes()).hexdigest()},
        "tasks": [
            {
                "task_id": "input",
                "input_xyz": str(input_link),
                "work_dir": str(attempt_root / "work"),
                "sha256": hashlib.sha256(outside_input.read_bytes()).hexdigest(),
            }
        ],
    }
    handoff_path = attempt_root / "handoff.json"
    handoff_path.write_bytes(_canonical(handoff))

    with pytest.raises(ValueError, match="non-symlink"):
        run_control_worker(
            state_root=root,
            run_id="worker-symlink-check",
            handoff_path=handoff_path,
            workflow_runner=lambda **_: {"ok": True},
        )


def test_control_worker_recovers_a_running_attempt_after_lease_loss(tmp_path: Path) -> None:
    config = tmp_path / "workflow.yaml"
    input_xyz = tmp_path / "input.xyz"
    config.write_text("steps: []\n", encoding="utf-8")
    input_xyz.write_text("1\nH\nH 0 0 0\n", encoding="utf-8")
    handoff = {
        "content_schema": HANDOFF_SCHEMA,
        "run_id": "worker-recover",
        "workflow_config": {"path": str(config), "sha256": hashlib.sha256(config.read_bytes()).hexdigest()},
        "tasks": [
            {
                "task_id": "input",
                "input_xyz": str(input_xyz),
                "work_dir": str(tmp_path / "work"),
                "sha256": hashlib.sha256(input_xyz.read_bytes()).hexdigest(),
            }
        ],
    }
    handoff_path = tmp_path / "handoff.json"
    handoff_path.write_bytes(_canonical(handoff))
    root = tmp_path / "state"
    service = open_control_service(root, identity_executable=sys.executable)
    identity = measure_executable(sys.executable)
    service.prepare(
        PrepareRequest(
            run_id="worker-recover",
            idempotency_key="worker-recover",
            request_digest="e" * 64,
            workflow_config_digest=handoff["workflow_config"]["sha256"],
            input_manifest_digest=hashlib.sha256(handoff_path.read_bytes()).hexdigest(),
            expected_executable_identity=identity,
        )
    )
    assert service.execute("worker-recover").state is RunState.QUEUED
    aggregate = service._repository.read("worker-recover")  # noqa: SLF001
    assert aggregate is not None and aggregate.launch_token is not None

    crash_worker = (
        "import os, sys; "
        "from confflow.control_worker import run_control_worker; "
        "run_control_worker(state_root=sys.argv[1], run_id=sys.argv[2], "
        "handoff_path=sys.argv[3], workflow_runner=lambda **_: os._exit(42))"
    )
    crashed = subprocess.run(
        [sys.executable, "-c", crash_worker, str(root), "worker-recover", str(handoff_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert crashed.returncode == 42
    abandoned = service._repository.read("worker-recover")  # noqa: SLF001
    assert abandoned is not None and abandoned.state is RunState.RUNNING

    state = run_control_worker(
        state_root=root,
        run_id="worker-recover",
        handoff_path=handoff_path,
        workflow_runner=lambda **kwargs: {"ok": True},
    )

    assert state is RunState.COMPLETED
    recovered = service._repository.read("worker-recover")  # noqa: SLF001
    assert recovered is not None
    assert [event.type for event in recovered.events] == [
        "prepared",
        "queued",
        "running",
        "requeued",
        "running",
        "completed",
    ]


def test_control_worker_keeps_paused_attempt_until_formal_resume(tmp_path: Path) -> None:
    """A pause is resumed through the producer service, not a second prepare."""
    config = tmp_path / "workflow.yaml"
    input_xyz = tmp_path / "input.xyz"
    work_dir = tmp_path / "work"
    config.write_text("steps: []\n", encoding="utf-8")
    input_xyz.write_text("1\nH\nH 0 0 0\n", encoding="utf-8")
    handoff = {
        "content_schema": HANDOFF_SCHEMA,
        "run_id": "worker-pause",
        "workflow_config": {"path": str(config), "sha256": hashlib.sha256(config.read_bytes()).hexdigest()},
        "tasks": [
            {
                "task_id": "input",
                "input_xyz": str(input_xyz),
                "work_dir": str(work_dir),
                "sha256": hashlib.sha256(input_xyz.read_bytes()).hexdigest(),
            }
        ],
    }
    handoff_path = tmp_path / "handoff.json"
    handoff_path.write_bytes(_canonical(handoff))
    root = tmp_path / "state"
    service = open_control_service(root, identity_executable=sys.executable)
    identity = measure_executable(sys.executable)
    service.prepare(
        PrepareRequest(
            run_id="worker-pause",
            idempotency_key="worker-pause",
            request_digest="d" * 64,
            workflow_config_digest=handoff["workflow_config"]["sha256"],
            input_manifest_digest=hashlib.sha256(handoff_path.read_bytes()).hexdigest(),
            expected_executable_identity=identity,
        )
    )
    assert service.execute("worker-pause").state is RunState.QUEUED

    calls = 0

    def pause_then_complete(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            kwargs["on_step_status_change"](
                SimpleNamespace(name="step", fail_count=0, status="completed")
            )
            raise StopRequestedError("pause")
        Path(kwargs["work_dir"]).mkdir(parents=True, exist_ok=True)
        return {"ok": True}

    def resume_after_pause(_seconds: float) -> None:
        open_control_service(root, identity_executable=sys.executable).resume(
            "worker-pause", checkpoint_id="checkpoint.step.0.completed"
        )

    state = run_control_worker(
        state_root=root,
        run_id="worker-pause",
        handoff_path=handoff_path,
        workflow_runner=pause_then_complete,
        sleep=resume_after_pause,
    )

    assert state is RunState.COMPLETED
    assert calls == 2
