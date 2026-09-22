"""Workflow state recovery tests for an interrupted supervisor process."""

from __future__ import annotations

from pathlib import Path

import pytest

from confflow.workflow.engine import run_workflow
from confflow.workflow.helpers import is_multi_frame_any
from confflow.workflow.state import WorkflowStateStore
from confflow.workflow.step_handlers import (
    StepExecutionResult,
    _build_confgen_run_kwargs,
    _compute_confgen_step_signature,
    _record_confgen_step_signature,
)


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


def _result(
    step_dir: str,
    name: str,
    current_input: str | list[str],
    params: dict,
    input_files: list[str],
    global_config: dict | None,
) -> StepExecutionResult:
    path = Path(step_dir) / "search.xyz"
    Path(step_dir).mkdir(parents=True, exist_ok=True)
    path.write_text(f"1\n{name}\nH 0 0 0\n", encoding="utf-8")
    signature = _compute_confgen_step_signature(
        current_input=current_input,
        input_files=input_files,
        run_kwargs=_build_confgen_run_kwargs(params, current_input, global_config),
        multi_frame=len(input_files) == 1 and is_multi_frame_any(current_input),
    )
    _record_confgen_step_signature(step_dir, signature)
    return StepExecutionResult(output_path=str(path))


def test_resume_reuses_completed_state_after_supervisor_disconnect(tmp_path, monkeypatch):
    input_xyz, config = _write_input_and_config(tmp_path)
    work_dir = tmp_path / "work"
    first_run_calls: list[str] = []

    def interrupted_confgen(step_dir, current_input, params, input_files, global_config=None):
        first_run_calls.append(Path(step_dir).name)
        if Path(step_dir).name == "second":
            raise RuntimeError("supervisor disconnected")
        return _result(step_dir, "first", current_input, params, input_files, global_config)

    monkeypatch.setattr("confflow.workflow.engine._run_confgen_step", interrupted_confgen)
    with pytest.raises(RuntimeError, match="supervisor disconnected"):
        run_workflow([str(input_xyz)], str(config), str(work_dir))

    interrupted_state = WorkflowStateStore(str(work_dir)).load()
    assert interrupted_state is not None
    assert interrupted_state.steps["first"].status == "completed"
    assert interrupted_state.steps["second"].status == "failed"

    resumed_calls: list[str] = []

    def resumed_confgen(step_dir, current_input, params, input_files, global_config=None):
        resumed_calls.append(Path(step_dir).name)
        return _result(
            step_dir,
            Path(step_dir).name,
            current_input,
            params,
            input_files,
            global_config,
        )

    monkeypatch.setattr("confflow.workflow.engine._run_confgen_step", resumed_confgen)
    stats = run_workflow([str(input_xyz)], str(config), str(work_dir), resume=True)

    assert first_run_calls == ["first", "second"]
    assert resumed_calls == ["second"]
    assert Path(stats["final_output"]).name == "search.xyz"
    completed_state = WorkflowStateStore(str(work_dir)).load()
    assert completed_state is not None
    assert completed_state.final_status == "completed"
    assert all(step.status == "completed" for step in completed_state.steps.values())
