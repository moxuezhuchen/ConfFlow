"""Boundary tests for the typed strict workflow resume policy."""

from __future__ import annotations

from pathlib import Path

import pytest

from confflow.contract import WORKFLOW_STATE_SCHEMA
from confflow.workflow.resume import (
    ResumeDiagnostic,
    ResumePolicy,
    create_initial_workflow_state,
)
from confflow.workflow.state import StepRecord


def _policy(
    tmp_path: Path,
    *,
    state: object,
    checkpoint: int = -1,
    resume: bool = True,
) -> ResumePolicy:
    steps = [{"name": "step", "type": "confgen"}]
    return ResumePolicy(
        resume=resume,
        resume_from_step=checkpoint,
        root_dir=str(tmp_path),
        global_config={},
        state=state,  # type: ignore[arg-type]
        steps=steps,
        step_dirnames=["step"],
    )


def test_resume_reuses_only_a_completed_saved_output(tmp_path: Path) -> None:
    output = tmp_path / "search.xyz"
    output.write_text("1\ncompleted\nH 0 0 0\n", encoding="utf-8")
    state = create_initial_workflow_state(
        root_dir=str(tmp_path),
        input_files=[str(tmp_path / "input.xyz")],
        original_inputs=[str(tmp_path / "input.xyz")],
        config_file=str(tmp_path / "workflow.yaml"),
        steps=[{"name": "step", "type": "confgen"}],
        step_dirnames=["step"],
    )
    state.steps["step"] = StepRecord(
        name="step",
        type="confgen",
        status="completed",
        output_xyz=str(output),
    )

    decision = _policy(tmp_path, state=state).decide(
        step_index=0,
        step_name="step",
        step={"name": "step", "type": "confgen"},
        step_dir=str(tmp_path / "step"),
        state_record=state.steps["step"],
        resolve_inputs=lambda _: str(tmp_path / "input.xyz"),
        current_input=str(tmp_path / "input.xyz"),
    )

    assert decision.action == "reuse"
    assert decision.output == str(output)


def test_resume_rejects_missing_completed_output_without_relaxing_criteria(
    tmp_path: Path,
) -> None:
    state = create_initial_workflow_state(
        root_dir=str(tmp_path),
        input_files=[str(tmp_path / "input.xyz")],
        original_inputs=[str(tmp_path / "input.xyz")],
        config_file=str(tmp_path / "workflow.yaml"),
        steps=[{"name": "step", "type": "confgen"}],
        step_dirnames=["step"],
    )
    state.steps["step"] = StepRecord(
        name="step",
        type="confgen",
        status="completed",
        output_xyz=str(tmp_path / "missing.xyz"),
    )

    with pytest.raises(RuntimeError, match="saved output is missing"):
        _policy(tmp_path, state=state).decide(
            step_index=0,
            step_name="step",
            step={"name": "step", "type": "confgen"},
            step_dir=str(tmp_path / "step"),
            state_record=state.steps["step"],
            resolve_inputs=lambda _: str(tmp_path / "input.xyz"),
            current_input=str(tmp_path / "input.xyz"),
        )


def test_resume_diagnostic_carries_state_schema_and_checkpoint() -> None:
    diagnostic = ResumeDiagnostic(
        step_index=2,
        step_name="calc",
        step_dir="/run/calc",
        reason="incomplete",
        checkpoint_index=1,
    )

    assert diagnostic.state_schema == WORKFLOW_STATE_SCHEMA
    assert diagnostic.state_version == 1
    assert diagnostic.checkpoint == 1
    assert "step 2 ('calc')" in diagnostic.failure_message()
