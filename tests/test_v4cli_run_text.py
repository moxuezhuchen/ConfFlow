"""Tests for ``confflow v4 run``: text mode prints per-step diagnostics; ``--json`` is unchanged."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from confflow import v4cli
from confflow.application import v4_run

WORKFLOW: dict[str, Any] = {
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

MANIFEST: dict[str, Any] = {
    "steps": [
        {
            "id": "cg",
            "status": "completed",
            "diagnostics": [
                {
                    "code": "confgen_donor_stretched",
                    "severity": "warning",
                    "message": "2 published structure(s) with a donor stretched",
                    "step_id": "cg",
                },
                {
                    "code": "confgen_input_gap",
                    "severity": "info",
                    "message": "the input structure is 1.50 kcal/mol above",
                    "step_id": "cg",
                },
                "not-a-diagnostic",
            ],
        },
        "not-a-step",
        {"id": "empty", "status": "completed"},
        {
            "id": "opt",
            "status": "failed",
            "diagnostics": [{"code": "boom", "severity": "error", "message": "it broke"}],
        },
    ]
}

TEXT_LINES = [
    "run run-1: {status}",
    "  cg: warning confgen_donor_stretched: 2 published structure(s) with a donor stretched",
    "  cg: info confgen_input_gap: the input structure is 1.50 kcal/mol above",
    "  opt: error boom: it broke",
]


class _Manifest:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def thaw(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._data))


def _patch_report(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    report = SimpleNamespace(
        run_id="run-1",
        status=status,
        definition_digest="sha256:abc",
        step_results=(),
        manifest=_Manifest(MANIFEST),
    )

    class _FakeApplication:
        def __init__(self, supervisor: Any) -> None:
            self.supervisor = supervisor

        def run(self, request: Any) -> Any:
            return report

    monkeypatch.setattr(v4_run, "V4RunApplication", _FakeApplication)


def _workflow(tmp_path: Path) -> Path:
    path = tmp_path / "flow.json"
    path.write_text(json.dumps(WORKFLOW), encoding="utf-8")
    return path


def _argv(tmp_path: Path, *extra: str) -> list[str]:
    return [
        "run",
        "--workflow",
        str(_workflow(tmp_path)),
        "--run-root",
        str(tmp_path / "r"),
        *extra,
    ]


def test_text_mode_prints_every_step_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    _patch_report(monkeypatch, "completed")

    code = v4cli.main(_argv(tmp_path))

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""
    assert captured.err.splitlines() == [line.format(status="completed") for line in TEXT_LINES]


def test_text_mode_failed_run_still_prints_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    _patch_report(monkeypatch, "failed")

    code = v4cli.main(_argv(tmp_path))

    captured = capsys.readouterr()
    assert code == 1
    assert captured.err.splitlines() == [line.format(status="failed") for line in TEXT_LINES]


def test_json_mode_output_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    _patch_report(monkeypatch, "completed")

    code = v4cli.main(_argv(tmp_path, "--json"))

    captured = capsys.readouterr()
    expected = {
        "run_id": "run-1",
        "status": "completed",
        "definition_digest": "sha256:abc",
        "steps": [],
        "manifest": MANIFEST,
    }
    assert code == 0
    assert captured.err == ""
    assert captured.out == json.dumps(expected, indent=2, sort_keys=True) + "\n"


def test_json_mode_failed_run_keeps_failures_and_no_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    _patch_report(monkeypatch, "failed")

    code = v4cli.main(_argv(tmp_path, "--json"))

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 1
    assert captured.err == ""
    assert payload["failures"] == [{"step_id": "opt", "code": "boom", "message": "it broke"}]


def test_text_mode_completed_run_without_diagnostics_prints_status_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    report = SimpleNamespace(
        run_id="run-2",
        status="completed",
        definition_digest="sha256:abc",
        step_results=(),
        manifest=_Manifest({"steps": [{"id": "s", "status": "completed"}]}),
    )

    class _FakeApplication:
        def __init__(self, supervisor: Any) -> None:
            pass

        def run(self, request: Any) -> Any:
            return report

    monkeypatch.setattr(v4_run, "V4RunApplication", _FakeApplication)

    assert v4cli.main(_argv(tmp_path)) == 0
    assert capsys.readouterr().err == "run run-2: completed\n"
