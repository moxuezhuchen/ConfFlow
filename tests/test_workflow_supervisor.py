"""Tests for polling and cancellation in ``WorkflowSupervisor``."""

from __future__ import annotations

from confflow.calc.executor import CalcStatus
from confflow.workflow.state import StepRecord, WorkflowState, WorkflowStateStore
from confflow.workflow.supervisor import WorkflowSupervisor


class StubExecutor:
    """Small poll-only executor used by the workflow supervisor tests."""

    def __init__(self, statuses: list[CalcStatus]) -> None:
        self.statuses = statuses
        self.handles = []

    def poll(self, handle):
        self.handles.append(handle)
        return self.statuses.pop(0)


def _submitted_state(work_dir: str) -> WorkflowState:
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


def test_supervisor_polls_until_submitted_step_completes(tmp_path):
    state = _submitted_state(str(tmp_path))
    WorkflowStateStore(str(tmp_path)).save(state)
    executor = StubExecutor(
        [
            CalcStatus(is_terminal=False, succeeded=False),
            CalcStatus(is_terminal=True, succeeded=True, exit_code=0),
        ]
    )
    observed: list[str] = []

    result = WorkflowSupervisor(
        str(tmp_path),
        calc_executor=executor,
        on_step_status_change=lambda step: observed.append(step.status),
        poll_interval_seconds=0,
    ).run_until_done_or_stopped()

    assert result.final_status == "completed"
    assert result.steps["calc"].status == "completed"
    assert observed == ["completed"]
    assert len(executor.handles) == 2
    assert executor.handles[0].executor_data == {"remote_job_id": "abc"}


def test_supervisor_persists_cancellation_before_polling(tmp_path):
    WorkflowStateStore(str(tmp_path)).save(_submitted_state(str(tmp_path)))
    executor = StubExecutor([])

    result = WorkflowSupervisor(
        str(tmp_path),
        calc_executor=executor,
        poll_interval_seconds=0,
    ).run_until_done_or_stopped(stop_check=lambda: True)

    assert result.final_status == "cancelled"
    assert executor.handles == []
    reloaded = WorkflowStateStore(str(tmp_path)).load()
    assert reloaded is not None
    assert reloaded.final_status == "cancelled"
