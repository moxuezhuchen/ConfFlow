#!/usr/bin/env python3

"""Private runtime orchestration for the public workflow entry point."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..calc.executor import CalcExecutor
from .executor import WorkflowExecutor
from .finalizer import finalize_workflow
from .planner import PreparedWorkflow
from .resume import ResumePolicy, create_initial_workflow_state
from .runtime_context import initialize_runtime_context
from .state import StepRecord, WorkflowStateStore
from .step_handlers import StepExecutionResult
from .validation import validate_inputs_compatible

StepHandler = Callable[..., StepExecutionResult]


class _WorkflowOrchestrator:
    """Own runtime setup, strict resume, execution, and finalization."""

    def __init__(
        self,
        *,
        prepared: PreparedWorkflow,
        config_file: str,
        work_dir: str,
        resume: bool,
        logger: Any,
        pause_beacon_file: str | None,
        step_started_callback: Callable[[str, str, str], None] | None,
        on_step_status_change: Callable[[StepRecord], None] | None,
        calc_executor: CalcExecutor | None,
        run_confgen_step: StepHandler,
        run_calc_step: StepHandler,
    ) -> None:
        self.prepared = prepared
        self.config_file = config_file
        self.work_dir = work_dir
        self.resume = resume
        self.logger = logger
        self.pause_beacon_file = pause_beacon_file
        self.step_started_callback = step_started_callback
        self.on_step_status_change = on_step_status_change
        self.calc_executor = calc_executor
        self.run_confgen_step = run_confgen_step
        self.run_calc_step = run_calc_step

    def run(self) -> dict[str, Any]:
        """Execute the prepared workflow and publish its final result."""
        self._validate_input_compatibility()

        runtime = initialize_runtime_context(
            work_dir=self.work_dir,
            config_file=self.config_file,
            input_files=self.prepared.input_files,
            original_inputs=self.prepared.original_inputs,
            resume=self.resume,
            logger=self.logger,
            global_config=self.prepared.global_config,
        )
        state_store = WorkflowStateStore(runtime.root_dir)
        state = state_store.load() if self.resume else None
        if state is None:
            state = create_initial_workflow_state(
                root_dir=runtime.root_dir,
                input_files=self.prepared.input_files,
                original_inputs=self.prepared.original_inputs,
                config_file=self.config_file,
                steps=self.prepared.steps,
                step_dirnames=self.prepared.step_dirnames,
            )
            state_store.save(state)

        resume_policy = ResumePolicy(
            resume=self.resume,
            resume_from_step=runtime.resume_from_step,
            root_dir=runtime.root_dir,
            global_config=self.prepared.global_config,
            state=state,
            steps=self.prepared.steps,
            step_dirnames=self.prepared.step_dirnames,
        )
        execution = WorkflowExecutor(
            prepared=self.prepared,
            runtime=runtime,
            state=state,
            state_store=state_store,
            resume_policy=resume_policy,
            pause_beacon_file=self.pause_beacon_file,
            step_started_callback=self.step_started_callback,
            on_step_status_change=self.on_step_status_change,
            calc_executor=self.calc_executor,
            run_confgen_step=self.run_confgen_step,
            run_calc_step=self.run_calc_step,
        ).execute()
        return finalize_workflow(
            root_dir=execution.root_dir,
            final_output=execution.final_output,
            original_inputs=self.prepared.original_inputs,
            terminal_outputs=execution.terminal_outputs,
            final_stats=execution.final_stats,
            state=execution.state,
            state_store=execution.state_store,
            execution_count=len(self.prepared.execution_order),
            logger=self.logger,
        )

    def _validate_input_compatibility(self) -> None:
        """Apply the existing multi-input compatibility contract."""
        if len(self.prepared.input_files) <= 1:
            return
        confgen_params = next(
            (
                step.get("params", {})
                for step in self.prepared.steps
                if step.get("type", "").lower() == "confgen"
            ),
            None,
        )
        validate_inputs_compatible(
            self.prepared.input_files,
            confgen_params,
            force_consistency=self.prepared.global_config.get("force_consistency", False),
        )
