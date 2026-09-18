#!/usr/bin/env python3

"""Workflow DAG edge cases: disabled terminals, duplicate deps, calc fan-in."""

from __future__ import annotations

from pathlib import Path

import pytest

from confflow.core.exceptions import ConfFlowError
from confflow.workflow.engine import run_workflow
from confflow.workflow.step_handlers import StepExecutionResult


def _patch_confgen(monkeypatch) -> dict[str, list]:
    captured: dict[str, list] = {"inputs": [], "dirs": []}

    def fake_confgen(step_dir, current_input, params, input_files, global_config=None):
        del params, input_files, global_config
        captured["dirs"].append(Path(step_dir).name)
        captured["inputs"].append(current_input)
        path = Path(step_dir) / "search.xyz"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("1\ngen\nH 0 0 0\n", encoding="utf-8")
        return StepExecutionResult(output_path=str(path))

    monkeypatch.setattr("confflow.workflow.engine._run_confgen_step", fake_confgen)
    return captured


def _write_input(tmp_path: Path) -> Path:
    path = tmp_path / "input.xyz"
    path.write_text("1\nseed\nH 0 0 0\n", encoding="utf-8")
    return path


def test_disabled_terminal_root_does_not_crash_finalize(tmp_path, monkeypatch):
    input_xyz = _write_input(tmp_path)
    config = tmp_path / "workflow.yaml"
    config.write_text(
        "global: {}\n"
        "steps:\n"
        "  - name: sink\n"
        "    type: confgen\n"
        "    enabled: false\n"
        "  - name: source\n"
        "    type: confgen\n"
        "  - name: child\n"
        "    type: confgen\n"
        "    inputs: [source]\n",
        encoding="utf-8",
    )
    work_dir = tmp_path / "work"
    _patch_confgen(monkeypatch)

    stats = run_workflow([str(input_xyz)], str(config), str(work_dir))

    assert Path(stats["final_output"]).name == "search.xyz"
    manifest = (work_dir / "output_manifest.json").read_text(encoding="utf-8")
    assert str(input_xyz) not in manifest


def test_duplicate_predecessors_are_deduped(tmp_path, monkeypatch):
    input_xyz = _write_input(tmp_path)
    config = tmp_path / "workflow.yaml"
    config.write_text(
        "global: {}\n"
        "steps:\n"
        "  - name: source\n"
        "    type: confgen\n"
        "  - name: join\n"
        "    type: confgen\n"
        "    inputs: [source, source]\n",
        encoding="utf-8",
    )
    work_dir = tmp_path / "work"
    captured = _patch_confgen(monkeypatch)

    run_workflow([str(input_xyz)], str(config), str(work_dir))

    join_inputs = [
        value for dirname, value in zip(captured["dirs"], captured["inputs"]) if dirname == "join"
    ]
    assert len(join_inputs) == 1
    assert isinstance(join_inputs[0], str)


def test_calc_fan_in_rejected_at_plan_time(tmp_path):
    input_xyz = _write_input(tmp_path)
    config = tmp_path / "workflow.yaml"
    config.write_text(
        "global: {}\n"
        "steps:\n"
        "  - name: left\n"
        "    type: confgen\n"
        "  - name: right\n"
        "    type: confgen\n"
        "  - name: join\n"
        "    type: calc\n"
        "    inputs: [left, right]\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfFlowError, match="exactly one input"):
        run_workflow([str(input_xyz)], str(config), str(tmp_path / "work"))
