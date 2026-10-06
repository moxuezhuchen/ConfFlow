"""Black-box tests for the packageable synthetic fixture executable."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import rfc8785

import tests.support.fixture_agent as fixture_module
from confflow.application.execution.workflow_adapter import measure_executable
from tests.support.fixture_agent import main as fixture_main
from tests.support.synthetic_producer import (
    SYNTHETIC_ARTIFACT,
    SYNTHETIC_ARTIFACT_PATH,
    SYNTHETIC_ARTIFACT_SCHEMA,
    SYNTHETIC_ARTIFACT_TERMINAL,
)


@pytest.fixture(autouse=True)
def _fixture_test_entrypoint(tmp_path: Path, monkeypatch):
    executable = tmp_path / "confflow-fixture-agent"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(sys, "argv", [str(executable)])


def _prepare_payload(
    run_id: str, identity: dict[str, str | None] | None = None
) -> dict[str, object]:
    measured = measure_executable(sys.argv[0])
    expected = identity or {
        "sha256": measured.sha256,
        "realpath": measured.realpath,
        "device_inode": measured.device_inode,
    }
    payload: dict[str, object] = {
        "protocol_schema": "confflow.control.v1",
        "operation": "prepare",
        "run_id": run_id,
        "idempotency_key": run_id,
        "workflow_config": {"path": "workflow.yaml", "sha256": "b" * 64},
        "input_manifest": {"path": "inputs/manifest.json", "sha256": "c" * 64},
        "expected_executable_identity": expected,
    }
    payload["request_digest"] = hashlib.sha256(rfc8785.dumps(payload)).hexdigest()
    return payload


def _invoke(capsys, args: list[str]) -> dict[str, object]:
    code = fixture_main(args)
    captured = capsys.readouterr()
    assert code == 0
    lines = captured.out.splitlines()
    assert len(lines) == 1, captured.out
    assert captured.err == ""
    return json.loads(lines[0])


def test_fixture_entrypoint_capabilities_bind_to_actual_executable(
    monkeypatch, capsys, tmp_path: Path
):
    executable = tmp_path / "confflow-fixture-agent"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(sys, "argv", [str(executable)])

    assert fixture_main(["--capabilities", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["executable"]["path"] == str(executable.resolve())
    assert payload["executable"]["realpath"] == str(executable.resolve())
    metadata = executable.stat()
    assert payload["executable"]["device_inode"] == f"{metadata.st_dev}:{metadata.st_ino}"
    assert payload["executable"]["sha256"] == hashlib.sha256(executable.read_bytes()).hexdigest()
    assert payload["executable"]["python"] == os.path.abspath(sys.executable)


def test_fixture_actual_entrypoint_prefers_windows_exe_sibling(monkeypatch, tmp_path: Path):
    """Bind a launcher that reports argv[0] without Windows' .exe suffix."""
    reported = tmp_path / "confflow-fixture-agent"
    invoked = reported.with_name(f"{reported.name}.exe")
    reported.write_text("launcher metadata", encoding="utf-8")
    invoked.write_bytes(b"MZ fixture launcher")
    host_os_name = os.name
    monkeypatch.setattr(
        fixture_module.cli,
        "os",
        SimpleNamespace(name="nt", fspath=os.fspath, path=os.path),
    )
    monkeypatch.setattr(fixture_module.sys, "argv", [str(reported)])

    assert fixture_module._actual_entrypoint() == str(invoked.resolve())
    assert os.name == host_os_name
    assert Path(os.fspath(tmp_path)).is_dir()


def test_fixture_actual_entrypoint_keeps_posix_reported_path_before_exe_sibling(
    monkeypatch, tmp_path: Path
):
    """A POSIX fixture launcher keeps its real no-suffix entrypoint."""
    reported = tmp_path / "confflow-fixture-agent"
    sibling = reported.with_name(f"{reported.name}.exe")
    reported.write_text("POSIX launcher", encoding="utf-8")
    sibling.write_bytes(b"not the POSIX launcher")
    host_os_name = os.name
    monkeypatch.setattr(
        fixture_module.cli,
        "os",
        SimpleNamespace(name="posix", fspath=os.fspath, path=os.path),
    )
    monkeypatch.setattr(fixture_module.sys, "argv", [str(reported)])

    assert fixture_module._actual_entrypoint() == str(reported.resolve())
    assert os.name == host_os_name
    assert Path(os.fspath(tmp_path)).is_dir()


def test_fixture_cli_runs_one_json_control_chain_to_fixed_manifest(capsys, tmp_path: Path):
    root = tmp_path / "state"
    run_id = "run-fixture-cli"
    request_path = tmp_path / "prepare.json"
    request_path.write_text(json.dumps(_prepare_payload(run_id)), encoding="utf-8")

    prepared = _invoke(
        capsys,
        [
            "control",
            "prepare",
            "--state-root",
            str(root),
            "--request",
            str(request_path),
            "--json",
        ],
    )
    assert prepared["state"] == "prepared"

    executed = _invoke(
        capsys,
        ["control", "execute", "--state-root", str(root), "--run-id", run_id, "--json"],
    )
    assert executed["state"] == "completed"
    assert executed["revision"] == 5

    status = _invoke(
        capsys,
        ["control", "status", "--state-root", str(root), "--run-id", run_id, "--json"],
    )
    assert status["state"] == "completed"

    events = _invoke(
        capsys,
        ["control", "events", "--state-root", str(root), "--run-id", run_id, "--json"],
    )
    assert [event["type"] for event in events["events"]] == [
        "prepared",
        "queued",
        "running",
        "checkpointed",
        "completed",
    ]
    assert events["next_cursor"] == "r00000000000000000005"

    artifacts = _invoke(
        capsys,
        ["control", "artifacts", "--state-root", str(root), "--run-id", run_id, "--json"],
    )
    assert artifacts["artifacts"] == [
        {
            "terminal": SYNTHETIC_ARTIFACT_TERMINAL,
            "path": SYNTHETIC_ARTIFACT_PATH,
            "sha256": SYNTHETIC_ARTIFACT.sha256,
            "size": SYNTHETIC_ARTIFACT.size,
            "content_schema": SYNTHETIC_ARTIFACT_SCHEMA,
        }
    ]


def test_fixture_cli_cancel_and_resume_use_standard_control_semantics(capsys, tmp_path: Path):
    root = tmp_path / "state"
    run_id = "run-fixture-cancel"
    request_path = tmp_path / "prepare.json"
    request_path.write_text(json.dumps(_prepare_payload(run_id)), encoding="utf-8")

    _invoke(
        capsys,
        [
            "control",
            "prepare",
            "--state-root",
            str(root),
            "--request",
            str(request_path),
            "--json",
        ],
    )
    cancelled = _invoke(
        capsys,
        ["control", "cancel", "--state-root", str(root), "--run-id", run_id, "--json"],
    )
    assert cancelled["state"] == "cancelled"

    code = fixture_main(
        ["control", "resume", "--state-root", str(root), "--run-id", run_id, "--json"]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert len(captured.out.splitlines()) == 1
    assert json.loads(captured.out)["error"]["code"] == "terminal_run"


def test_fixture_cli_reuses_typed_invalid_request_response(capsys, tmp_path: Path):
    code = fixture_main(
        [
            "control",
            "execute",
            "--state-root",
            str(tmp_path / "state"),
            "--run-id",
            "run-invalid",
            "--unexpected",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert code == 1
    assert len(captured.out.splitlines()) == 1
    assert json.loads(captured.out)["error"]["code"] == "invalid_request"


def test_fixture_cli_reuses_typed_error_response_for_unknown_run(capsys, tmp_path: Path):
    code = fixture_main(
        [
            "control",
            "status",
            "--state-root",
            str(tmp_path / "state"),
            "--run-id",
            "missing-run",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert len(captured.out.splitlines()) == 1
    response = json.loads(captured.out)
    assert response["ok"] is False
    assert response["error"]["code"] == "unknown_run"
