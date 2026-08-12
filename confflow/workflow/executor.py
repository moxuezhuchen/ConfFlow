#!/usr/bin/env python3

"""Workflow step executor and durable execution boundary."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..calc.executor import CalcExecutor
from ..core import io as io_xyz
from ..core.exceptions import StopRequestedError
from ..core.types import TaskStatus
from ..core.utils import index_to_letter_prefix
from .planner import PreparedWorkflow
from .presenter import print_step_footer_block, print_step_header_block, print_workflow_start
from .resume import ResumeDecision, ResumePolicy
from .runtime_context import WorkflowRuntimeContext
from .state import StepRecord, WorkflowState, WorkflowStateStore
from .stats import TaskStatsCollector
from .step_handlers import StepExecutionResult
from .step_handlers import run_calc_step as default_run_calc_step
from .step_handlers import run_confgen_step as default_run_confgen_step

__all__ = [
    "WorkflowExecution",
    "WorkflowExecutor",
    "mark_step_completed",
    "notify_step_status_change",
]


StepOutput = str | list[str]
StepHandler = Callable[..., StepExecutionResult]


@dataclass(frozen=True)
class WorkflowExecution:
    """Outputs needed by the orchestration and finalization stages."""

    root_dir: str
    final_output: StepOutput
    terminal_outputs: dict[str, list[str]]
    final_stats: dict[str, Any]
    state: WorkflowState
    state_store: WorkflowStateStore


class WorkflowExecutor:
    """Execute a prepared workflow while owning lineage and durable mutation."""

    def __init__(
        self,
        *,
        prepared: PreparedWorkflow,
        runtime: WorkflowRuntimeContext,
        state: WorkflowState,
        state_store: WorkflowStateStore,
        resume_policy: ResumePolicy,
        pause_beacon_file: str | None = None,
        step_started_callback: Callable[[str, str, str], None] | None = None,
        on_step_status_change: Callable[[StepRecord], None] | None = None,
        calc_executor: CalcExecutor | None = None,
        run_confgen_step: StepHandler = default_run_confgen_step,
        run_calc_step: StepHandler = default_run_calc_step,
    ) -> None:
        self.prepared = prepared
        self.runtime = runtime
        self.state = state
        self.state_store = state_store
        self.resume_policy = resume_policy
        self.pause_beacon_file = pause_beacon_file
        self.step_started_callback = step_started_callback
        self.on_step_status_change = on_step_status_change
        self.calc_executor = calc_executor
        self.run_confgen_step = run_confgen_step
        self.run_calc_step = run_calc_step
        self.lineage: dict[str, StepOutput] = {}

    def execute(self) -> WorkflowExecution:
        """Run all planned steps and return the data consumed by finalizer."""
        current_input = self.runtime.current_input
        self.runtime.stats_tracker.stats["initial_conformers"] = self._count(current_input)
        print_workflow_start(self.prepared.input_files, current_input)

        for execution_index, step_name in enumerate(self.prepared.execution_order):
            step = self.prepared.by_step_name[step_name]
            step_dirname = self.prepared.name_to_dirname[step_name]
            step_dir = os.path.join(self.runtime.root_dir, step_dirname)
            state_record = self.state.steps[step_dirname]

            decision = self.resume_policy.decide(
                step_index=execution_index,
                step_name=step_name,
                step=step,
                step_dir=step_dir,
                state_record=state_record,
                resolve_inputs=self._resolve_inputs_for_step,
                current_input=current_input,
            )
            if decision.action == "reuse":
                current_input = self._require_output(decision)
                self.lineage[step_name] = current_input
                continue
            if decision.action == "skip":
                current_input = self._require_output(decision)
                self.lineage[step_name] = current_input
                if not self.resume_policy.resume or state_record.status not in {
                    "completed",
                    "skipped",
                }:
                    state_record.status = "skipped"
                    notify_step_status_change(self.on_step_status_change, state_record)
                    self.state_store.save(self.state)
                continue

            if self.pause_beacon_file and os.path.exists(self.pause_beacon_file):
                raise StopRequestedError(f"Pause beacon found at {self.pause_beacon_file}")

            if not step.get("enabled", True):
                current_input = self._resolve_with_fallback(step_name, current_input)
                self.lineage[step_name] = current_input
                state_record.status = "skipped"
                notify_step_status_change(self.on_step_status_change, state_record)
                self.state_store.save(self.state)
                continue

            current_input = self._resolve_inputs_for_step(step_name)
            self.lineage[step_name] = current_input
            self._execute_step(
                execution_index=execution_index,
                step_name=step_name,
                step=step,
                step_dir=step_dir,
                state_record=state_record,
                inputs_for_step=current_input,
            )
            current_input = self.lineage[step_name]

        terminal_outputs = {
            name: _as_artifact_list(self.lineage[name]) for name in self.prepared.terminal_steps
        }
        final_outputs = [artifact for artifacts in terminal_outputs.values() for artifact in artifacts]
        final_output = (
            self.lineage[self.prepared.terminal_steps[0]]
            if len(self.prepared.terminal_steps) == 1
            else final_outputs
        )
        final_stats = self.runtime.stats_tracker.finalize(final_output)
        return WorkflowExecution(
            root_dir=self.runtime.root_dir,
            final_output=final_output,
            terminal_outputs=terminal_outputs,
            final_stats=final_stats,
            state=self.state,
            state_store=self.state_store,
        )

    def _resolve_inputs_for_step(self, step_name: str) -> StepOutput:
        predecessors = self.prepared.predecessors[step_name]
        if not predecessors:
            initial = self.runtime.current_input
            return list(initial) if isinstance(initial, list) else initial

        outputs: list[str] = []
        for predecessor in predecessors:
            output = self.lineage.get(predecessor)
            if output is None:
                raise RuntimeError(
                    f"step {step_name!r} depends on {predecessor!r} but that step produced no output"
                )
            if isinstance(output, list):
                outputs.extend(output)
            else:
                outputs.append(output)
        return outputs[0] if len(outputs) == 1 else outputs

    def _resolve_with_fallback(self, step_name: str, current_input: StepOutput) -> StepOutput:
        try:
            return self._resolve_inputs_for_step(step_name)
        except RuntimeError:
            return current_input

    def _execute_step(
        self,
        *,
        execution_index: int,
        step_name: str,
        step: dict[str, Any],
        step_dir: str,
        state_record: StepRecord,
        inputs_for_step: StepOutput,
    ) -> None:
        step_type = step["type"]
        os.makedirs(step_dir, exist_ok=True)
        step_start = time.time()
        in_n = self._count(inputs_for_step)
        step_stats: dict[str, Any] = {
            "name": step_name,
            "type": step_type,
            "index": execution_index + 1,
            "input_conformers": in_n,
            "start_time": datetime.now().isoformat(),
        }
        params = step.get("params", {}) or {}

        # Preserve the existing callback contract: the launcher callback sees the
        # directory before the persisted submitted state notification.
        if self.step_started_callback:
            self.step_started_callback(step_name, step_type, step_dir)
        state_record.status = "submitted"
        state_record.submitted_at = time.time()
        state_record.error = None
        self.state.wavefront_index = execution_index
        self.state_store.save(self.state)
        notify_step_status_change(self.on_step_status_change, state_record)

        print_step_header_block(
            step_index=execution_index + 1,
            total_steps=len(self.prepared.steps),
            step_name=step_name,
            step_type=step_type,
            global_config=self.prepared.global_config,
            params=params,
            in_count=in_n,
        )

        current_input = inputs_for_step
        try:
            if step_type in ["confgen", "gen"]:
                step_result = self.run_confgen_step(
                    step_dir,
                    inputs_for_step,
                    params,
                    self.prepared.input_files,
                    self.prepared.global_config,
                )
                current_input = step_result.output_path
                io_xyz.ensure_xyz_cids(current_input, prefix=index_to_letter_prefix(0))
                if step_result.copied_multi_frame:
                    step_stats["status"] = TaskStatus.SKIPPED_MULTI
                elif step_result.reused_existing:
                    step_stats["status"] = TaskStatus.SKIPPED
                else:
                    step_stats["status"] = TaskStatus.COMPLETED
            elif step_type in ["calc", "task"]:
                calc_kwargs: dict[str, Any] = {
                    "step_dir": step_dir,
                    "current_input": inputs_for_step,
                    "params": params,
                    "global_config": self.prepared.global_config,
                    "root_dir": self.runtime.root_dir,
                    "steps": self.prepared.steps,
                    "failure_tracker": self.runtime.failure_tracker,
                    "step_name": step_name,
                }
                if self.calc_executor is not None:
                    calc_kwargs["calc_executor"] = self.calc_executor
                step_result = self.run_calc_step(**calc_kwargs)
                current_input = step_result.output_path
                io_xyz.ensure_xyz_cids(current_input, prefix=index_to_letter_prefix(0))
                step_stats["status"] = (
                    TaskStatus.SKIPPED if step_result.reused_existing else TaskStatus.COMPLETED
                )

            self.lineage[step_name] = current_input
            step_stats["output_xyz"] = (
                os.path.abspath(current_input) if isinstance(current_input, str) else current_input
            )
        except Exception as error:
            # Any handler failure is persisted before it is allowed to abort the pipeline.
            step_stats["status"] = TaskStatus.FAILED
            step_stats["error"] = str(error)
            state_record.status = "failed"
            state_record.error = str(error)
            state_record.fail_count += 1
            state_record.completed_at = time.time()
            self.state.final_status = "failed"
            self.state_store.save(self.state)
            notify_step_status_change(self.on_step_status_change, state_record)
            self.runtime.checkpoint.save(execution_index - 1, self.runtime.stats_tracker.get_stats())
            raise
        finally:
            step_stats["end_time"] = datetime.now().isoformat()
            step_stats["duration_seconds"] = round(time.time() - step_start, 2)
            step_stats["output_conformers"] = self._count(current_input)
            failed_count = 0
            if step_type in ["calc", "task"]:
                db_path = os.path.join(step_dir, "results.db")
                failed_count = TaskStatsCollector.count_failed(db_path) or 0
                step_stats["failed_conformers"] = failed_count
            print_step_footer_block(
                step_stats=step_stats,
                in_count=in_n,
                failed_count=failed_count,
            )
            self.runtime.stats_tracker.add_step(step_stats)
            if step_stats["status"] in [
                TaskStatus.COMPLETED,
                TaskStatus.SKIPPED,
                TaskStatus.SKIPPED_MULTI,
            ]:
                self.runtime.checkpoint.save(execution_index, self.runtime.stats_tracker.get_stats())
                mark_step_completed(self.state, state_record, current_input, execution_index)
                self.state_store.save(self.state)
                notify_step_status_change(self.on_step_status_change, state_record)

    @staticmethod
    def _count(value: StepOutput) -> int:
        from .helpers import count_conformers_any

        return count_conformers_any(value)

    @staticmethod
    def _require_output(decision: ResumeDecision) -> StepOutput:
        if decision.output is None:
            raise RuntimeError("Workflow resume decision did not include an output")
        return decision.output


def mark_step_completed(
    state: WorkflowState,
    record: StepRecord,
    current_input: StepOutput,
    index: int,
) -> None:
    """Persist the common completed-step mutation used by the executor."""
    record.status = "completed"
    record.completed_at = time.time()
    record.output_xyz = current_input if isinstance(current_input, str) else None
    record.error = None
    state.wavefront_index = index + 1


def notify_step_status_change(
    callback: Callable[[StepRecord], None] | None,
    record: StepRecord,
) -> None:
    """Invoke the optional status callback at the established mutation point."""
    if callback:
        callback(record)


def _as_artifact_list(output: StepOutput) -> list[str]:
    """Normalize a terminal step output to absolute artifact paths."""
    if isinstance(output, str):
        return [os.path.abspath(output)]
    return [os.path.abspath(path) for path in output if isinstance(path, str)]
