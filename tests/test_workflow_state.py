"""Tests for the restartable workflow state file."""

from __future__ import annotations

import json

import pytest

from confflow.workflow.state import StepRecord, WorkflowState, WorkflowStateStore


def _state(work_dir: str) -> WorkflowState:
    return WorkflowState(
        run_id="run-123",
        work_dir=work_dir,
        input_files=["/inputs/seed.xyz"],
        original_inputs=["/inputs/seed.xyz"],
        config_file="/inputs/workflow.yaml",
        steps={
            "calc": StepRecord(
                name="calc",
                type="calc",
                status="submitted",
                submitted_at=123.0,
                executor_handle_data={"remote_job_id": "abc"},
            )
        },
    )


def test_state_store_save_is_atomic_and_loads_records(tmp_path):
    store = WorkflowStateStore(str(tmp_path))
    state = _state(str(tmp_path))

    store.save(state)

    assert (tmp_path / ".workflow_state.json").exists()
    assert not (tmp_path / ".workflow_state.json.tmp").exists()
    raw = json.loads((tmp_path / ".workflow_state.json").read_text(encoding="utf-8"))
    assert raw["content_schema"] == "confflow.workflow_state.v1"
    assert raw["steps"]["calc"]["executor_handle_data"] == {"remote_job_id": "abc"}

    loaded = store.load()
    assert loaded is not None
    assert loaded.run_id == "run-123"
    assert loaded.steps["calc"].status == "submitted"
    assert loaded.steps["calc"].executor_handle_data == {"remote_job_id": "abc"}


def test_state_store_returns_none_when_no_state_exists(tmp_path):
    assert WorkflowStateStore(str(tmp_path)).load() is None


def test_state_store_rejects_invalid_json(tmp_path):
    state_file = tmp_path / ".workflow_state.json"
    state_file.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid workflow state file"):
        WorkflowStateStore(str(tmp_path)).load()
