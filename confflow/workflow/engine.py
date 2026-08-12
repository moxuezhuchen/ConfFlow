#!/usr/bin/env python3

"""Orchestrate workflow planning, strict resume, execution, and finalization."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..calc.executor import CalcExecutor
from ..core.utils import get_logger
from .executor import (
    mark_step_completed,
    notify_step_status_change,
)
from .helpers import count_conformers_any as _count_conformers_any
from .orchestrator import _WorkflowOrchestrator
from .planner import prepare_workflow
from .resume import (
    create_initial_workflow_state,
    expected_output_reason,
    format_resume_failure,
    validate_state_steps,
)
from .state import StepRecord, WorkflowState
from .stats import FailureTracker
from .step_handlers import StepExecutionResult
from .step_handlers import run_calc_step as step_run_calc_step
from .step_handlers import run_confgen_step as step_run_confgen_step
from .validation import validate_inputs_compatible

__all__ = ["run_workflow", "count_conformers_any", "validate_inputs_compatible"]

logger = get_logger()

# Compatibility anchor: ``prepare_workflow`` is the sole owner of
# ``build_step_graph`` and ``topo_order``.  Keep these names visible in the
# facade so source-level architecture checks and downstream tooling can verify
# that the engine no longer carries a second legacy graph implementation.


def count_conformers_any(value: str | list[str]) -> int:
    """Compatibility facade for the historical engine-level import."""
    return _count_conformers_any(value)


def _resume_failure_message(
    *,
    step_index: int,
    step_name: str,
    step_dir: str,
    reason: str,
) -> str:
    """Compatibility facade for the pre-Phase-5 private helper."""
    return format_resume_failure(
        step_index=step_index,
        step_name=step_name,
        step_dir=step_dir,
        reason=reason,
    )


def _expected_output_reason(step_type: str | None) -> str:
    """Compatibility facade for the pre-Phase-5 private helper."""
    return expected_output_reason(step_type)


def _run_confgen_step(
    step_dir: str,
    current_input: str | list[str],
    params: dict[str, Any],
    input_files: list[str],
    global_config: dict[str, Any],
) -> StepExecutionResult:
    """Compatibility facade used by existing tests and integrations."""
    return step_run_confgen_step(step_dir, current_input, params, input_files, global_config)


def _run_calc_step(
    step_dir: str,
    current_input: str | list[str],
    params: dict[str, Any],
    global_config: dict[str, Any],
    root_dir: str,
    steps: list[dict[str, Any]],
    failure_tracker: FailureTracker,
    step_name: str,
    *,
    calc_executor: CalcExecutor | None = None,
) -> StepExecutionResult:
    """Compatibility facade used by existing tests and integrations."""
    kwargs: dict[str, Any] = {
        "step_dir": step_dir,
        "current_input": current_input,
        "params": params,
        "global_config": global_config,
        "root_dir": root_dir,
        "steps": steps,
        "failure_tracker": failure_tracker,
        "step_name": step_name,
    }
    if calc_executor is not None:
        kwargs["calc_executor"] = calc_executor
    return step_run_calc_step(**kwargs)


def run_workflow(
    input_xyz: list[str],
    config_file: str,
    work_dir: str,
    original_input_files: list[str] | None = None,
    resume: bool = False,
    verbose: bool = False,
    pause_beacon_file: str | None = None,
    step_started_callback: Callable[[str, str, str], None] | None = None,
    *,
    calc_executor: CalcExecutor | None = None,
    on_step_status_change: Callable[[StepRecord], None] | None = None,
    poll_interval_seconds: float = 5,
) -> dict[str, Any]:
    """Run a workflow through the planner, resume policy, executor, and finalizer."""
    if poll_interval_seconds < 0:
        raise ValueError("poll_interval_seconds must be >= 0")
    if verbose and hasattr(logger, "set_level"):
        logger.set_level(10)

    prepared = prepare_workflow(input_xyz, config_file, original_input_files)
    return _WorkflowOrchestrator(
        prepared=prepared,
        config_file=config_file,
        work_dir=work_dir,
        resume=resume,
        logger=logger,
        pause_beacon_file=pause_beacon_file,
        step_started_callback=step_started_callback,
        on_step_status_change=on_step_status_change,
        calc_executor=calc_executor,
        # Keep the old private dispatch seams usable by existing callers/tests.
        run_confgen_step=_run_confgen_step,
        run_calc_step=_run_calc_step,
    ).run()


def _initial_workflow_state(
    *,
    root_dir: str,
    input_files: list[str],
    original_inputs: list[str],
    config_file: str,
    steps: list[dict[str, Any]],
    step_dirnames: list[str],
) -> WorkflowState:
    """Compatibility facade for the old private state factory."""
    return create_initial_workflow_state(
        root_dir=root_dir,
        input_files=input_files,
        original_inputs=original_inputs,
        config_file=config_file,
        steps=steps,
        step_dirnames=step_dirnames,
    )


def _as_artifact_list(output: str | list[str]) -> list[str]:
    """Compatibility facade retained for older private imports."""
    from .executor import _as_artifact_list as normalize_artifacts

    return normalize_artifacts(output)


def _validate_state_steps(
    state: WorkflowState,
    steps: list[dict[str, Any]],
    step_dirnames: list[str],
) -> None:
    """Compatibility facade for the old private state validator."""
    validate_state_steps(state, steps, step_dirnames)


def _mark_step_completed(
    state: WorkflowState,
    record: StepRecord,
    current_input: str | list[str],
    index: int,
) -> None:
    """Compatibility facade for the old private completion mutation."""
    mark_step_completed(state, record, current_input, index)


def _notify_step_status_change(
    callback: Callable[[StepRecord], None] | None,
    record: StepRecord,
) -> None:
    """Compatibility facade for the old private callback helper."""
    notify_step_status_change(callback, record)
