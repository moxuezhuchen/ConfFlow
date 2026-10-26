"""Tests for ``confflow v4 validate``: optional ``--json`` and the human-readable text output."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from confflow import v4cli
from confflow.producer.validation import ValidationReport
from confflow.workflow.v4.diagnostics import DiagnosticCode

VALID_WORKFLOW: dict[str, Any] = {
    "schema": "confflow.workflow.v4",
    "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
    "steps": [
        {
            "id": "s",
            "executor": "structure_transform",
            "transform": {"kind": "filter"},
            "bindings": {"structure": {"source": {"run": "structures"}}},
        }
    ],
}


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_valid_workflow_without_json_prints_ok_line(tmp_path: Path, capsys: Any) -> None:
    workflow = _write(tmp_path, "flow.json", json.dumps(VALID_WORKFLOW))

    assert v4cli.main(["validate", "--workflow", str(workflow)]) == 0

    captured = capsys.readouterr()
    assert captured.out == "OK: workflow is valid\n"
    assert captured.err == ""


def test_invalid_workflow_without_json_prints_error_to_stderr(tmp_path: Path, capsys: Any) -> None:
    workflow = _write(tmp_path, "flow.yaml", "schema: confflow.workflow.v4\n")

    code = v4cli.main(["validate", "--workflow", str(workflow)])

    captured = capsys.readouterr()
    assert code != 0
    assert captured.out == ""
    assert captured.err.startswith("Error: schema_error: ")
    assert all(line.startswith("Error: ") for line in captured.err.splitlines())


def test_valid_workflow_with_json_is_unchanged(tmp_path: Path, capsys: Any) -> None:
    workflow = _write(tmp_path, "flow.json", json.dumps(VALID_WORKFLOW))

    assert v4cli.main(["validate", "--workflow", str(workflow), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "confflow.configuration-validation.v1"
    assert payload["ok"] is True


def test_invalid_workflow_with_json_is_unchanged(tmp_path: Path, capsys: Any) -> None:
    workflow = _write(tmp_path, "flow.yaml", "schema: confflow.workflow.v4\n")

    code = v4cli.main(["validate", "--workflow", str(workflow), "--json"])

    captured = capsys.readouterr()
    assert code == 1
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert payload["diagnostics"]
    assert captured.err == ""


def test_warnings_are_printed_on_success(
    tmp_path: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _write(tmp_path, "flow.json", json.dumps(VALID_WORKFLOW))
    warning = {
        "code": DiagnosticCode.SCIENTIFIC_PARAMETER_CONFLICT.value,
        "severity": "warning",
        "step_id": None,
        "field_path": "calculation.overrides.charge",
        "message": "step charge override replaces the structure's explicit charge",
        "reason": None,
    }
    report = ValidationReport(ok=True, diagnostics=(warning,), step_ids=("s",))
    monkeypatch.setattr("confflow.producer.validation.validate_workflow_bytes", lambda data: report)

    assert v4cli.main(["validate", "--workflow", str(workflow)]) == 0

    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        "OK: workflow is valid",
        f"Warning: {warning['code']}: {warning['message']}",
    ]
    assert captured.err == ""


def test_errors_are_printed_to_stderr_on_failure(
    tmp_path: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = _write(tmp_path, "flow.json", json.dumps(VALID_WORKFLOW))
    error = {
        "code": "schema_error",
        "severity": "error",
        "step_id": None,
        "field_path": "<document>",
        "message": "synthetic failure",
        "reason": None,
    }
    warning = dict(error, severity="warning", message="ignored on failure")
    report = ValidationReport(ok=False, diagnostics=(warning, error))
    monkeypatch.setattr("confflow.producer.validation.validate_workflow_bytes", lambda data: report)

    assert v4cli.main(["validate", "--workflow", str(workflow)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Error: schema_error: synthetic failure\n"


def test_validate_parses_without_json_flag() -> None:
    parser = v4cli._build_parser()

    args = parser.parse_args(["validate", "--stdin"])

    assert args.command == "validate"
    assert args.json is False
