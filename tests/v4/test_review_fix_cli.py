"""Regression tests for review findings #4 (CLI re-run identity) and #6 (duplicate keys)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from confflow import v4cli
from confflow.application.v4_entry import load_v4_document_file
from confflow.core.exceptions import ConfFlowError

REPO_ROOT = Path(__file__).resolve().parents[2]
XYZ = "1\nreview\nHe 0 0 0\n"


def _document() -> dict:
    return {
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


def _write_inputs(root: Path) -> Path:
    xyz = root / "input.xyz"
    xyz.write_text(XYZ, encoding="utf-8")
    return xyz


def _run_args(workflow: Path, xyz: Path, run_root: Path) -> list[str]:
    return [
        "run",
        "--workflow",
        str(workflow),
        "--inputs",
        f"structures={xyz}",
        "--run-root",
        str(run_root),
        "--json",
    ]


def test_identical_cli_run_twice_on_one_run_root_succeeds(tmp_path, capsys):
    workflow = tmp_path / "flow.json"
    workflow.write_text(json.dumps(_document()), encoding="utf-8")
    xyz = _write_inputs(tmp_path)
    args = _run_args(workflow, xyz, tmp_path / "run")

    first_code = v4cli.main(args)
    capsys.readouterr()
    second_code = v4cli.main(args)
    second = json.loads(capsys.readouterr().out)

    assert first_code == 0
    assert second_code == 0, second.get("failures")
    assert second["status"] == "completed"
    assert "failures" not in second


def test_duplicate_key_workflow_fails_run_like_validate(tmp_path, capsys):
    text = json.dumps(_document()).replace(
        '"kind": "filter"', '"kind": "deduplicate", "kind": "filter"'
    )
    workflow = tmp_path / "dup.json"
    workflow.write_text(text, encoding="utf-8")
    xyz = _write_inputs(tmp_path)
    run_root = tmp_path / "run"

    validate_code = v4cli.main(["validate", "--workflow", str(workflow), "--json"])
    validated = json.loads(capsys.readouterr().out)
    run_code = v4cli.main(_run_args(workflow, xyz, run_root))
    captured = capsys.readouterr()

    assert validate_code == 1
    assert [item["code"] for item in validated["diagnostics"]] == ["schema_error"]
    assert run_code == 1
    assert "schema_error" in captured.err
    assert "duplicate key" in captured.err
    assert not run_root.exists()


def test_duplicate_key_yaml_workflow_fails_run(tmp_path, capsys):
    workflow = tmp_path / "dup.yaml"
    workflow.write_text(
        "schema: confflow.workflow.v4\nschema: confflow.workflow.v4\n", encoding="utf-8"
    )
    xyz = _write_inputs(tmp_path)

    code = v4cli.main(_run_args(workflow, xyz, tmp_path / "run"))
    captured = capsys.readouterr()

    assert code == 1
    assert "duplicate key" in captured.err


def test_load_v4_document_file_rejects_duplicate_keys(tmp_path):
    workflow = tmp_path / "dup.json"
    workflow.write_text(
        '{"schema": "confflow.workflow.v4", "schema": "confflow.workflow.v4"}', encoding="utf-8"
    )

    with pytest.raises(ConfFlowError, match="duplicate key"):
        load_v4_document_file(workflow)


def test_load_v4_document_file_still_accepts_json(tmp_path):
    workflow = tmp_path / "flow.json"
    workflow.write_text(json.dumps(_document()), encoding="utf-8")

    assert load_v4_document_file(workflow)["schema"] == "confflow.workflow.v4"


def test_tomli_is_a_runtime_dependency_for_old_pythons():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert (
        "\"tomli>=2.0.1; python_version < '3.11'\""
        in text.split("[project.optional-dependencies]")[0]
    )


def test_real_gaussian_gate_is_closed_without_opt_in():
    """Import the module only (no test runs) and read its skip condition."""
    env = {key: value for key, value in os.environ.items() if key != "CONFFLOW_ALLOW_REAL_QC"}
    code = (
        "from tests.v4 import test_gaussian_real_differential as module; "
        "print(module.pytestmark.args[0])"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    # The skipif condition must be True (skip) before /opt/g16 is consulted.
    assert result.stdout.strip() == "True"
