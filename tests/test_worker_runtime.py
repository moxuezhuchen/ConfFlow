"""Focused tests for extracted worker publication and invocation boundaries."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from confflow.application.execution.models import ExecutableIdentity
from confflow.application.execution.state_root import StateRoot
from confflow.application.execution.workflow_adapter import measure_executable
from confflow.worker_runner import (
    VerifiedWorkerHandoff,
    VerifiedWorkerLaunch,
    WorkerWorkflowRunnerAdapter,
)
from confflow.worker_security import _canonical_json
from confflow.worker_sidecar import WorkerSidecarPublisher


def _verified_bindings() -> tuple[VerifiedWorkerHandoff, VerifiedWorkerLaunch]:
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "control_protocol"
        / "v1"
        / "golden"
        / "worker_handoff.json"
    )
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    payload.pop("_schema")
    digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
    return (
        VerifiedWorkerHandoff(run_id="fixture-run", digest=digest),
        VerifiedWorkerLaunch(
            run_id="fixture-run",
            token="fixture-run.launch.1",
            expected_identity=measure_executable(sys.executable),
        ),
    )


def test_runner_adapter_invokes_engine_then_publishes_sidecars(tmp_path: Path) -> None:
    handoff, launch = _verified_bindings()
    input_xyz = tmp_path / "methane.xyz"
    input_xyz.write_text("1\nH\nH 0 0 0\n", encoding="utf-8")
    events: list[str] = []
    publisher = WorkerSidecarPublisher.__new__(WorkerSidecarPublisher)

    def runner(**kwargs: object) -> dict[str, bool]:
        events.append("engine")
        assert kwargs["original_input_files"] == [str(input_xyz)]
        print("engine report")
        return {"ok": True}

    def publish(_publisher: object, **kwargs: str) -> None:
        events.append("sidecars")
        assert kwargs == {"staged_input": str(input_xyz), "work_dir": str(tmp_path / "work")}

    adapter = WorkerWorkflowRunnerAdapter(
        runner,
        handoff=handoff,
        launch=launch,
        original_input=str(input_xyz),
        work_dir=str(tmp_path / "work"),
        sidecar_publisher=publisher,
        publish_sidecars=publish,
    )

    assert adapter(original_input_files=[str(input_xyz)]) == {"ok": True}
    assert events == ["engine", "sidecars"]
    assert (tmp_path / "methane.txt").read_text(encoding="utf-8") == "engine report\n"


def test_runner_binding_rejects_unverified_or_incomplete_identity() -> None:
    handoff, _ = _verified_bindings()
    with pytest.raises(TypeError, match="verified launch"):
        WorkerWorkflowRunnerAdapter(
            lambda **_: None,
            handoff=handoff,
            launch=object(),  # type: ignore[arg-type]
            original_input="/tmp/input.xyz",
            work_dir="/tmp/work",
            sidecar_publisher=object(),  # type: ignore[arg-type]
        )

    with pytest.raises(ValueError, match="complete executable identity"):
        VerifiedWorkerLaunch(
            run_id="fixture-run",
            token="fixture-run.launch.1",
            expected_identity=ExecutableIdentity(sha256="a" * 64),
        )


@pytest.mark.skipif(os.name != "posix", reason="atomic publication requires POSIX ownership checks")
def test_sidecar_atomic_publication_failure_preserves_existing_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.xyz"
    destination = tmp_path / "result.xyz"
    source.write_text("new\n", encoding="utf-8")
    destination.write_text("old\n", encoding="utf-8")
    publisher = WorkerSidecarPublisher.__new__(WorkerSidecarPublisher)
    expected = hashlib.sha256(source.read_bytes()).hexdigest()

    def fail_replace(*_: object) -> None:
        raise OSError("destination replaced concurrently")

    monkeypatch.setattr("confflow.worker_sidecar.os.replace", fail_replace)
    with pytest.raises(OSError, match="replaced concurrently"):
        publisher._atomic_copy(source, destination, expected_digest=expected)  # noqa: SLF001

    assert destination.read_text(encoding="utf-8") == "old\n"
    assert list(tmp_path.glob(".result.xyz.*.tmp")) == []


@pytest.mark.skipif(os.name != "posix", reason="sidecar path checks require POSIX ownership checks")
def test_sidecar_publisher_rejects_path_escape_and_digest_mismatch(tmp_path: Path) -> None:
    attempt = tmp_path / "attempt"
    state = attempt / "state"
    attempt.mkdir(mode=0o700)
    state.mkdir(mode=0o700)
    outside = tmp_path / "outside.xyz"
    outside.write_text("outside\n", encoding="utf-8")
    publisher = WorkerSidecarPublisher(StateRoot.resolve(state))

    with pytest.raises(ValueError, match="below the worker attempt root"):
        publisher.publish(staged_input=str(outside), work_dir=str(attempt / "work"))

    source = attempt / "input.xyz"
    source.write_text("input\n", encoding="utf-8")
    destination = attempt / "result.xyz"
    with pytest.raises(ValueError, match="changed while being published"):
        publisher._atomic_copy(  # noqa: SLF001
            source,
            destination,
            expected_digest="0" * 64,
        )
    assert not destination.exists()
