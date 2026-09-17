#!/usr/bin/env python3

"""Regression tests for the machine-readable configuration CLI."""

from __future__ import annotations

import io
import json
from unittest.mock import Mock

from confflow.cli import main
from confflow.core.contracts import ExitCode


def _one_json_document(capsys):
    captured = capsys.readouterr()
    assert captured.err == ""
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert len(lines) == 1
    return json.loads(lines[0])


def test_config_contract_json_is_one_document_and_does_not_run_workflow(monkeypatch, capsys):
    run_workflow = Mock()
    monkeypatch.setattr("confflow.cli.run_workflow", run_workflow)

    result = main(["config", "contract", "--json"])

    assert result == ExitCode.SUCCESS
    payload = _one_json_document(capsys)
    assert payload["schema"] == "confflow.configuration-contract.v1"
    assert payload["validation_response_schema"] == "confflow.configuration-validation.v1"
    assert payload["workflow_schema_sha256"]
    run_workflow.assert_not_called()


def test_config_validate_json_success_is_structured_and_does_not_run_workflow(monkeypatch, capsys):
    run_workflow = Mock()
    monkeypatch.setattr("confflow.cli.run_workflow", run_workflow)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"global": {}, "steps": []})))

    result = main(["config", "validate", "--json", "--stdin"])

    assert result == ExitCode.SUCCESS
    payload = _one_json_document(capsys)
    assert payload["schema"] == "confflow.configuration-validation.v1"
    assert payload["valid"] is True
    assert payload["issues"] == []
    assert payload["workflow_schema_sha256"]
    run_workflow.assert_not_called()


def test_config_validate_json_error_is_structured_and_does_not_run_workflow(monkeypatch, capsys):
    run_workflow = Mock()
    monkeypatch.setattr("confflow.cli.run_workflow", run_workflow)
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"global": {}, "steps": "not-a-list"})),
    )

    result = main(["config", "validate", "--json", "--stdin"])

    assert result == ExitCode.USAGE_ERROR
    payload = _one_json_document(capsys)
    assert payload["schema"] == "confflow.configuration-validation.v1"
    assert payload["valid"] is False
    assert len(payload["issues"]) == 1
    assert payload["issues"][0]["path"] == ""
    assert "steps" in payload["issues"][0]["message"]
    run_workflow.assert_not_called()
