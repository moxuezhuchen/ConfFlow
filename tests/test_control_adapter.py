"""Phase D control adapter contract and subprocess tests."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
import rfc8785

from confflow.application.execution import (
    Artifact,
    ArtifactManifest,
    ErrorCode,
    EventPage,
    ExecutionEvent,
    ExecutionServiceError,
    RunSnapshot,
    RunState,
)
from confflow.control import main


@dataclass
class FakeService:
    """Service double that exposes the Phase C public operation boundary."""

    snapshot: RunSnapshot = RunSnapshot("run-1", 3, RunState.RUNNING)

    def prepare(self, request):
        self.prepare_request = request
        return RunSnapshot(request.run_id, 1, RunState.PREPARED)

    def execute(self, run_id):
        self.execute_run_id = run_id
        return RunSnapshot(run_id, 2, RunState.QUEUED)

    def status(self, run_id):
        self.status_run_id = run_id
        return self.snapshot

    def events(self, run_id, *, after=None):
        self.events_args = (run_id, after)
        return EventPage(
            snapshot=self.snapshot,
            events=(ExecutionEvent("r00000000000000000003", 3, "running"),),
            next_cursor="r00000000000000000003",
        )

    def cancel(self, run_id):
        self.cancel_run_id = run_id
        return RunSnapshot(run_id, 4, RunState.CANCELLED)

    def resume(self, run_id, *, checkpoint_id=None):
        self.resume_args = (run_id, checkpoint_id)
        return RunSnapshot(run_id, 5, RunState.QUEUED)

    def artifacts(self, run_id):
        self.artifacts_run_id = run_id
        return ArtifactManifest(
            snapshot=RunSnapshot(run_id, 6, RunState.COMPLETED),
            artifacts=(
                Artifact("z_terminal", "z/out", "f" * 64, 2, "text/plain"),
                Artifact("a_terminal", "a/out", "e" * 64, 1, "text/plain"),
            ),
        )


def _capture(monkeypatch: pytest.MonkeyPatch, service: FakeService, args: list[str], capsys):
    monkeypatch.setattr("confflow.control.open_control_service", lambda root: service)
    code = main(args)
    captured = capsys.readouterr()
    return code, json.loads(captured.out), captured.err


def _prepare_payload() -> dict:
    payload = {
        "protocol_schema": "confflow.control.v1",
        "operation": "prepare",
        "run_id": "run-1",
        "idempotency_key": "key-1",
        "request_digest": "0" * 64,
        "workflow_config": {"path": "workflow.yaml", "sha256": "b" * 64},
        "input_manifest": {"path": "inputs/manifest.json", "sha256": "c" * 64},
        "expected_executable_identity": {"sha256": "d" * 64},
    }
    semantic = dict(payload)
    semantic.pop("request_digest")
    payload["request_digest"] = hashlib.sha256(rfc8785.dumps(semantic)).hexdigest()
    return payload


def test_capabilities_is_one_stable_protocol_json_response(capsys):
    """Capabilities does not open state and emits no human-readable output."""
    assert main(["capabilities", "--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "ok": True,
        "operation": "capabilities",
        "protocol_schema": "confflow.control.v1",
        "supported_protocols": ["confflow.control.v1"],
    }


def test_all_operations_map_to_the_service_public_methods(monkeypatch, capsys, tmp_path: Path):
    """The adapter translates protocol frames without owning execution state."""
    service = FakeService()
    for operation, extra in (
        ("execute", []),
        ("status", []),
        ("events", ["--after", "r00000000000000000002"]),
        ("cancel", []),
        ("resume", ["--checkpoint", "checkpoint-1"]),
        ("artifacts", []),
    ):
        code, response, _ = _capture(
            monkeypatch,
            service,
            [operation, "--state-root", str(tmp_path), "--run-id", "run-1", *extra, "--json"],
            capsys,
        )
        assert code == 0
        assert response["operation"] == operation
        assert response["ok"] is True

    assert service.execute_run_id == "run-1"
    assert service.status_run_id == "run-1"
    assert service.events_args == ("run-1", "r00000000000000000002")
    assert service.cancel_run_id == "run-1"
    assert service.resume_args == ("run-1", "checkpoint-1")
    assert [
        item["terminal"]
        for item in _capture(
            monkeypatch,
            service,
            ["artifacts", "--state-root", str(tmp_path), "--run-id", "run-1", "--json"],
            capsys,
        )[1]["artifacts"]
    ] == ["a_terminal", "z_terminal"]


def test_prepare_decodes_schema_and_maps_the_complete_service_request(
    monkeypatch, capsys, tmp_path: Path
):
    """Prepare forwards the frozen identifiers and all three digest bindings."""
    service = FakeService()
    request_path = tmp_path / "request.json"
    payload = _prepare_payload()
    request_path.write_text(json.dumps(payload), encoding="utf-8")

    code, response, _ = _capture(
        monkeypatch,
        service,
        ["prepare", "--state-root", str(tmp_path), "--request", str(request_path), "--json"],
        capsys,
    )

    assert code == 0
    assert response["state"] == "prepared"
    assert service.prepare_request.run_id == "run-1"
    assert service.prepare_request.workflow_config_digest == "b" * 64
    assert service.prepare_request.input_manifest_digest == "c" * 64
    assert service.prepare_request.expected_executable_identity.sha256 == "d" * 64


@pytest.mark.parametrize(
    ("payload", "code_name"),
    [
        (b"", "invalid_request"),
        (b"{} {}", "invalid_request"),
        (
            b'{"protocol_schema":"confflow.control.v2","operation":"capabilities"}',
            "unsupported_protocol",
        ),
    ],
)
def test_malformed_empty_extra_and_unknown_protocol_are_typed_errors(
    monkeypatch, capsys, tmp_path: Path, payload: bytes, code_name: str
):
    """Malformed request files never reach the service and always return JSON."""
    request_path = tmp_path / "request.json"
    request_path.write_bytes(payload)
    code, response, _ = _capture(
        monkeypatch,
        FakeService(),
        ["prepare", "--state-root", str(tmp_path), "--request", str(request_path), "--json"],
        capsys,
    )
    assert code == 1
    assert response["ok"] is False
    assert response["error"]["code"] == code_name


def test_service_error_preserves_typed_code_retryable_and_runtime_exit(
    monkeypatch, capsys, tmp_path: Path
):
    """Service errors are encoded without leaking a traceback to stdout."""
    error = ExecutionServiceError(ErrorCode.REPOSITORY_UNAVAILABLE, "locked", retryable=True)

    class FailingService(FakeService):
        def status(self, run_id):
            raise error

    code, response, _ = _capture(
        monkeypatch,
        FailingService(),
        ["status", "--state-root", str(tmp_path), "--run-id", "run-1", "--json"],
        capsys,
    )
    assert code == 2
    assert response["error"] == {
        "code": "repository_unavailable",
        "message": "locked",
        "retryable": True,
    }


def test_installed_console_entrypoint_has_pure_stdout():
    """The real installed entrypoint supports the protocol subprocess boundary."""
    completed = subprocess.run(
        [str(Path(sys.executable).with_name("confflow")), "control", "capabilities", "--json"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])},
    )
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["supported_protocols"] == ["confflow.control.v1"]
    assert completed.stderr == "" or "Numba not found" in completed.stderr
