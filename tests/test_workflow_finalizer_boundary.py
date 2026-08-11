"""Boundary tests for final workflow state and artifact publication."""

from __future__ import annotations

from pathlib import Path

from confflow.workflow.finalizer import finalize_workflow
from confflow.workflow.state import StepRecord, WorkflowState, WorkflowStateStore


class _Logger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def debug(self, message: str) -> None:
        self.messages.append(message)


def test_finalizer_commits_terminal_state_before_publishing_statistics(
    tmp_path: Path, monkeypatch
) -> None:
    state = WorkflowState(
        run_id="run-1",
        work_dir=str(tmp_path),
        input_files=[str(tmp_path / "input.xyz")],
        original_inputs=[str(tmp_path / "input.xyz")],
        config_file=str(tmp_path / "workflow.yaml"),
        steps={"step": StepRecord(name="step", type="calc", status="running")},
    )
    store = WorkflowStateStore(str(tmp_path))
    logger = _Logger()
    monkeypatch.setattr("confflow.workflow.finalizer.Tracer.trace_low_energy", lambda stats: None)
    monkeypatch.setattr(
        "confflow.workflow.finalizer.emit_final_report_and_lowest",
        lambda *args: None,
    )
    monkeypatch.setattr(
        "confflow.workflow.finalizer.write_final_statistics",
        lambda root, stats: None,
    )

    stats = finalize_workflow(
        root_dir=str(tmp_path),
        final_output=str(tmp_path / "output.xyz"),
        original_inputs=state.original_inputs,
        terminal_outputs={"step": [str(tmp_path / "output.xyz")]},
        final_stats={"steps": []},
        state=state,
        state_store=store,
        execution_count=1,
        logger=logger,
    )

    assert stats["terminal_outputs"] == {"step": [str(tmp_path / "output.xyz")]}
    assert state.final_status == "completed"
    assert state.wavefront_index == 1
