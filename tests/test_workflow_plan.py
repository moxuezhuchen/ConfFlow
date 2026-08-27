"""Characterization tests for the pure workflow planning boundary."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, is_dataclass
from pathlib import Path

import pytest

from confflow.config.models import GlobalOptions
from confflow.workflow.plan import WorkflowPlan, build_workflow_plan


def _write_xyz(path: Path, label: str = "seed") -> None:
    path.write_text(f"1\n{label}\nH 0 0 0\n", encoding="utf-8")


def test_build_workflow_plan_normalizes_inputs_and_explicit_dag(tmp_path: Path) -> None:
    input_xyz = tmp_path / "input.xyz"
    original_xyz = tmp_path / "original.xyz"
    _write_xyz(input_xyz)
    _write_xyz(original_xyz, "original")
    config = tmp_path / "workflow.yaml"
    config.write_text(
        "global:\n"
        "  force_consistency: true\n"
        "steps:\n"
        "  - name: join output\n"
        "    type: calc\n"
        "    inputs: [left, right]\n"
        "    params: {keyword: HF}\n"
        "  - name: right\n"
        "    type: confgen\n"
        "    inputs: [root]\n"
        "  - name: left\n"
        "    type: confgen\n"
        "    inputs: [root]\n"
        "  - name: root\n"
        "    type: confgen\n",
        encoding="utf-8",
    )

    plan = build_workflow_plan(
        [str(input_xyz)], str(config), original_input_files=[str(original_xyz)]
    )

    assert isinstance(plan, WorkflowPlan)
    assert is_dataclass(plan)
    assert plan.input_files == [str(input_xyz.resolve())]
    assert plan.original_inputs == [str(original_xyz.resolve())]
    assert isinstance(plan.typed_global, GlobalOptions)
    assert plan.global_config["force_consistency"] is True
    assert [step["name"] for step in plan.steps] == [
        "join output",
        "right",
        "left",
        "root",
    ]
    assert plan.workflow.steps[-1].name == "root"
    assert plan.predecessors == {
        "join output": ["left", "right"],
        "right": ["root"],
        "left": ["root"],
        "root": [],
    }
    assert plan.execution_order == ["root", "left", "right", "join output"]
    assert plan.terminal_steps == ["join output"]
    assert plan.step_dirnames == ["join_output", "right", "left", "root"]
    assert plan.name_to_dirname == {
        "join output": "join_output",
        "right": "right",
        "left": "left",
        "root": "root",
    }

    with pytest.raises(FrozenInstanceError):
        plan.input_files = []  # type: ignore[misc]


def test_build_workflow_plan_keeps_legacy_linear_fallback_and_shape(tmp_path: Path) -> None:
    input_xyz = tmp_path / "input.xyz"
    _write_xyz(input_xyz)
    config = tmp_path / "workflow.yaml"
    config.write_text(
        "global: {}\n"
        "steps:\n"
        "  - name: first\n"
        "    type: confgen\n"
        "  - name: second\n"
        "    type: calc\n"
        "    params: {keyword: HF}\n",
        encoding="utf-8",
    )

    plan = build_workflow_plan([str(input_xyz)], str(config))

    assert plan.original_inputs is plan.input_files
    assert plan.predecessors == {"first": [], "second": ["first"]}
    assert plan.execution_order == ["first", "second"]
    assert plan.terminal_steps == ["second"]
    assert plan.step_dirnames == ["first", "second"]
    assert plan.steps[1] == {
        "name": "second",
        "type": "calc",
        "enabled": True,
        "params": {"keyword": "HF"},
    }
