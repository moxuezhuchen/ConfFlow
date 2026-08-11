"""Boundary tests for the extracted workflow planning stage."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from confflow.workflow.planner import prepare_workflow


def _write_config(path: Path, steps: str) -> Path:
    config = path / "workflow.yaml"
    config.write_text(f"global: {{}}\nsteps:\n{steps}", encoding="utf-8")
    return config


def _write_xyz(path: Path, name: str = "input.xyz") -> Path:
    input_file = path / name
    input_file.write_text("1\nseed\nH 0 0 0\n", encoding="utf-8")
    return input_file


def test_prepare_workflow_preserves_legacy_linear_order(tmp_path: Path) -> None:
    input_file = _write_xyz(tmp_path)
    config = _write_config(
        tmp_path,
        """  - name: first
    type: confgen
  - name: second
    type: calc
  - name: third
    type: calc
""",
    )

    prepared = prepare_workflow([str(input_file)], str(config))

    assert prepared.execution_order == ["first", "second", "third"]
    assert prepared.predecessors == {
        "first": [],
        "second": ["first"],
        "third": ["second"],
    }
    assert prepared.terminal_steps == ["third"]
    assert prepared.step_dirnames == ["first", "second", "third"]
    assert prepared.name_to_dirname == {
        "first": "first",
        "second": "second",
        "third": "third",
    }


def test_prepare_workflow_builds_explicit_fan_out_and_fan_in(tmp_path: Path) -> None:
    input_file = _write_xyz(tmp_path)
    config = _write_config(
        tmp_path,
        """  - name: source
    type: confgen
    inputs: []
  - name: left
    type: calc
    inputs: [source]
  - name: right
    type: calc
    inputs: [source]
  - name: merge
    type: calc
    inputs: [left, right]
""",
    )

    prepared = prepare_workflow([str(input_file)], str(config))

    assert prepared.execution_order == ["source", "left", "right", "merge"]
    assert prepared.predecessors == {
        "source": [],
        "left": ["source"],
        "right": ["source"],
        "merge": ["left", "right"],
    }
    assert prepared.terminal_steps == ["merge"]


def test_prepare_workflow_reports_missing_input_with_absolute_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing.xyz"
    config = _write_config(
        tmp_path,
        """  - name: only
    type: calc
""",
    )

    message = f"Input file does not exist: {missing}"
    with pytest.raises(FileNotFoundError, match=re.escape(message)):
        prepare_workflow([str(missing)], str(config))
