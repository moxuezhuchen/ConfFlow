"""Workflow state recovery tests for an interrupted supervisor process."""

from __future__ import annotations

from pathlib import Path

import pytest

from confflow.workflow.engine import run_workflow
from confflow.workflow.state import WorkflowStateStore
from confflow.workflow.step_handlers import StepExecutionResult


def _write_input_and_config(tmp_path):
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("1\nseed\nH 0 0 0\n", encoding="utf-8")
    config = tmp_path / "workflow.yaml"
    config.write_text(
        "global: {}\n"
        "steps:\n"
        "  - name: first\n"
        "    type: confgen\n"
        "    params: {}\n"
        "  - name: second\n"
        "    type: confgen\n"
        "    params: {}\n",
        encoding="utf-8",
    )
    return input_xyz, config


def _result(step_dir: str, name: str) -> StepExecutionResult:
    path = Path(step_dir) / "search.xyz"
    Path(step_dir).mkdir(parents=True, exist_ok=True)
    path.write_text(f"1\n{name}\nH 0 0 0\n", encoding="utf-8")
    return StepExecutionResult(output_path=str(path))


def test_resume_reuses_completed_state_after_supervisor_disconnect(tmp_path, monkeypatch):
    input_xyz, config = _write_input_and_config(tmp_path)
    work_dir = tmp_path / "work"
    first_run_calls: list[str] = []

    def interrupted_confgen(step_dir, current_input, params, input_files, global_config=None):
        del current_input, params, input_files, global_config
        first_run_calls.append(Path(step_dir).name)
        if Path(step_dir).name == "second":
            raise RuntimeError("supervisor disconnected")
        return _result(step_dir, "first")

    monkeypatch.setattr("confflow.workflow.engine._run_confgen_step", interrupted_confgen)
    with pytest.raises(RuntimeError, match="supervisor disconnected"):
        run_workflow([str(input_xyz)], str(config), str(work_dir))

    interrupted_state = WorkflowStateStore(str(work_dir)).load()
    assert interrupted_state is not None
    assert interrupted_state.steps["first"].status == "completed"
    assert interrupted_state.steps["second"].status == "failed"

    resumed_calls: list[str] = []

    def resumed_confgen(step_dir, current_input, params, input_files, global_config=None):
        del current_input, params, input_files, global_config
        resumed_calls.append(Path(step_dir).name)
        return _result(step_dir, Path(step_dir).name)

    monkeypatch.setattr("confflow.workflow.engine._run_confgen_step", resumed_confgen)
    stats = run_workflow([str(input_xyz)], str(config), str(work_dir), resume=True)

    assert first_run_calls == ["first", "second"]
    assert resumed_calls == ["second"]
    assert Path(stats["final_output"]).name == "search.xyz"
    completed_state = WorkflowStateStore(str(work_dir)).load()
    assert completed_state is not None
    assert completed_state.final_status == "completed"
    assert all(step.status == "completed" for step in completed_state.steps.values())
