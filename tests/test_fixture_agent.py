"""Black-box tests for the packageable synthetic fixture executable."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import rfc8785

from confflow.application.execution.synthetic_producer import (
    SYNTHETIC_ARTIFACT,
    SYNTHETIC_ARTIFACT_PATH,
    SYNTHETIC_ARTIFACT_SCHEMA,
    SYNTHETIC_ARTIFACT_TERMINAL,
)
from confflow.application.execution.workflow_adapter import measure_executable
from confflow.fixture_agent import main as fixture_main


def _prepare_payload(run_id: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "protocol_schema": "confflow.control.v1",
        "operation": "prepare",
        "run_id": run_id,
        "idempotency_key": run_id,
        "workflow_config": {"path": "workflow.yaml", "sha256": "b" * 64},
        "input_manifest": {"path": "inputs/manifest.json", "sha256": "c" * 64},
        "expected_executable_identity": {
            "sha256": measure_executable(sys.executable).sha256,
        },
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
    assert payload["executable"]["sha256"] == hashlib.sha256(executable.read_bytes()).hexdigest()
    assert payload["executable"]["python"] == os.path.abspath(sys.executable)


def test_fixture_console_script_is_declared_as_a_package_entrypoint():
    pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert 'confflow-fixture-agent = "confflow.fixture_agent:main"' in pyproject


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
