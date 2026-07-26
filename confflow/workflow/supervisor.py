#!/usr/bin/env python3

"""Polling supervisor for externally submitted workflow calculations."""

from __future__ import annotations

import time
from collections.abc import Callable

from ..calc.executor import CalcExecutor, CalcHandle, LocalCalcExecutor
from .state import StepRecord, WorkflowState, WorkflowStateStore

__all__ = ["WorkflowSupervisor"]

_SUCCESSFUL_STEP_STATUSES = {"completed", "skipped"}
_TERMINAL_STEP_STATUSES = _SUCCESSFUL_STEP_STATUSES | {"failed"}


class WorkflowSupervisor:
    """Persistently poll submitted steps until completion, failure, or cancellation.

    A remote executor must place JSON-serializable handle data in
    :attr:`StepRecord.executor_handle_data`.  This lets a newly created
    supervisor rebuild a :class:`CalcHandle` after its predecessor exits.
    ``LocalCalcExecutor`` remains useful for one-process callers, but its live
    ``Popen`` handle is intentionally not restartable.
    """

    def __init__(
        self,
        work_dir: str,
        calc_executor: CalcExecutor | None = None,
        on_step_status_change: Callable[[StepRecord], None] | None = None,
        poll_interval_seconds: float = 5,
    ) -> None:
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds must be >= 0")
        self.store = WorkflowStateStore(work_dir)
        self.calc_executor = calc_executor or LocalCalcExecutor()
        self.on_step_status_change = on_step_status_change
        self.poll_interval_seconds = poll_interval_seconds

    def run_until_done_or_stopped(
        self,
        stop_check: Callable[[], bool] | None = None,
    ) -> WorkflowState:
        """Poll submitted steps and save each terminal state transition.

        The method returns unchanged if there are pending steps but no submitted
        handles.  Submitting those steps remains the workflow engine's job.
        """
        state = self.store.load()
        if state is None:
            raise RuntimeError("No workflow state exists to supervise")

        while not _is_terminal(state):
            if stop_check and stop_check():
                state.final_status = "cancelled"
                self.store.save(state)
                return state

            submitted = [step for step in state.steps.values() if step.status == "submitted"]
            if not submitted:
                return state

            observed_running = False
            for step in submitted:
                status = self.calc_executor.poll(_handle_for_step(state, step))
                if not status.is_terminal:
                    observed_running = True
                    continue

                step.status = "completed" if status.succeeded else "failed"
                step.error = status.error
                step.completed_at = time.time()
                if not status.succeeded:
                    step.fail_count += 1
                self.store.save(state)
                if self.on_step_status_change:
                    self.on_step_status_change(step)

            _set_final_status_if_terminal(state)
            if state.final_status:
                self.store.save(state)
                return state
            if observed_running:
                time.sleep(self.poll_interval_seconds)

        return state


def _handle_for_step(state: WorkflowState, step: StepRecord) -> CalcHandle:
    data = step.executor_handle_data or {}
    return CalcHandle(
        job_name=step.name,
        work_dir=str(data.get("work_dir", state.work_dir)),
        submitted_at=step.submitted_at or state.started_at,
        executor_data=data,
    )


def _is_terminal(state: WorkflowState) -> bool:
    return bool(state.final_status) or all(
        step.status in _TERMINAL_STEP_STATUSES for step in state.steps.values()
    )


def _set_final_status_if_terminal(state: WorkflowState) -> None:
    statuses = {step.status for step in state.steps.values()}
    if not statuses:
        state.final_status = "completed"
    elif statuses <= _SUCCESSFUL_STEP_STATUSES:
        state.final_status = "completed"
    elif "failed" in statuses and statuses <= _TERMINAL_STEP_STATUSES:
        state.final_status = "failed"
