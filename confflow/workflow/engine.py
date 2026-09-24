#!/usr/bin/env python3

"""Run the workflow without calling ``sys.exit`` directly."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ..calc.artifacts import (
    CalcArtifactManager,
    CalcResumeCompatibilityError,
    PreparedCalcArtifacts,
    compute_input_digest,
)
from ..calc.executor import CalcExecutor
from ..config.canonical import build_workflow_binding, require_executable, resolve_calc_step
from ..config.models import GlobalOptions
from ..core import io as io_xyz
from ..core.exceptions import ConfFlowError, StopRequestedError
from ..core.path_policy import resolve_sandbox_root, validate_managed_path
from ..core.types import TaskStatus
from ..core.utils import (
    get_logger,
    index_to_letter_prefix,
)
from .finalize import finalize_workflow as _finalize_workflow_impl
from .helpers import count_conformers_any, resolve_step_output
from .plan import WorkflowPlan, build_workflow_plan, workflow_plan_source_version
from .presenter import (
    emit_final_report_and_lowest,
    print_step_footer_block,
    print_step_header_block,
    print_workflow_start,
    write_final_statistics,
)
from .resume_validation import ResumeArtifactCompatibilityError, validate_reusable_artifact
from .runtime_context import initialize_runtime_context
from .state import StepRecord, WorkflowState, WorkflowStateStore
from .stats import (
    CheckpointManager,
    FailureTracker,
    TaskStatsCollector,
    Tracer,
)
from .step_handlers import StepExecutionResult, _resolve_chk_input_dir
from .step_handlers import run_calc_step as step_run_calc_step
from .step_handlers import run_confgen_step as step_run_confgen_step
from .validation import validate_inputs_compatible

__all__ = [
    "run_workflow",
    "finalize_workflow",
    "validate_inputs_compatible",
]

logger = get_logger()


def _resume_failure_message(
    *,
    step_index: int,
    step_name: str,
    step_dir: str,
    reason: str,
) -> str:
    return (
        f"Resume failed: step {step_index} ('{step_name}') cannot be reused: {reason}. "
        "Strict resume does not automatically re-run stale or incomplete steps. "
        f"Next action: back up or remove {step_dir}, then run again without --resume "
        "if recomputing this step is intended."
    )


def _expected_output_reason(step_type: str | None) -> str:
    st = (step_type or "").lower()
    if st in {"calc", "task"}:
        return "missing expected output file output.xyz or result.xyz"
    if st in {"confgen", "gen"}:
        return "missing expected output file search.xyz"
    return "missing expected step output"


def _run_confgen_step(
    step_dir: str,
    current_input: str | list[str],
    params: dict[str, Any],
    input_files: list[str],
    global_config: dict[str, Any],
) -> StepExecutionResult:
    """Execute a conformer generation step."""
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
    typed_global: GlobalOptions | None = None,
    calc_executor: CalcExecutor | None = None,
    cancel_beacon_file: str | None = None,
) -> StepExecutionResult:
    """Execute a calculation task step."""
    if calc_executor is not None:
        return step_run_calc_step(
            step_dir=step_dir,
            current_input=current_input,
            params=params,
            global_config=global_config,
            root_dir=root_dir,
            steps=steps,
            failure_tracker=failure_tracker,
            step_name=step_name,
            typed_global=typed_global,
            calc_executor=calc_executor,
            cancel_beacon_file=cancel_beacon_file,
        )
    return step_run_calc_step(
        step_dir=step_dir,
        current_input=current_input,
        params=params,
        global_config=global_config,
        root_dir=root_dir,
        steps=steps,
        failure_tracker=failure_tracker,
        step_name=step_name,
        typed_global=typed_global,
        cancel_beacon_file=cancel_beacon_file,
    )


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
    cancel_beacon_file: str | None = None,
) -> dict[str, Any]:
    """Run a workflow while recording restartable step state.

    Existing callers retain the original positional and keyword parameters.
    ``calc_executor`` is optional and is forwarded only to calculation steps;
    omitting it preserves the local execution behaviour.
    """
    if poll_interval_seconds < 0:
        raise ValueError("poll_interval_seconds must be >= 0")
    if verbose and hasattr(logger, "set_level"):
        logger.set_level(10)

    plan = build_workflow_plan(
        input_xyz,
        config_file,
        original_input_files=original_input_files,
    )
    # Mandatory execution-capability guard (R3.5): planning is legal for V3,
    # nothing after this point is. This must precede the binding, resume
    # prevalidation, runtime initialization and any state/dirname mutation.
    require_executable(workflow_plan_source_version(plan))
    if not isinstance(plan, WorkflowPlan):
        # Unreachable while CAPABILITIES gates V3 execution off; kept as the
        # narrowing boundary so V3 runtime concepts cannot leak into this V1
        # engine even if the table is later edited carelessly.
        raise ConfFlowError(
            "Workflow V3 execution requires the R4 state/binding runtime; "
            f"refused after planning ({plan.source_version})."
        )
    input_files = plan.input_files
    original_inputs = plan.original_inputs
    global_config = plan.global_config
    typed_global = plan.typed_global
    steps = plan.steps
    by_step_name = plan.by_step_name
    predecessors = plan.predecessors
    execution_order = plan.execution_order
    terminal_steps = plan.terminal_steps
    step_dirnames = plan.step_dirnames
    name_to_dirname = plan.name_to_dirname

    config_binding = build_workflow_binding(plan)
    # Validate durable state before runtime initialization, so resume cannot
    # inspect, reuse, or clean an artifact under an untrusted configuration.
    prevalidated_root = validate_managed_path(
        work_dir,
        label="work_dir",
        sandbox_root=resolve_sandbox_root(global_config),
    )
    preloaded_state = WorkflowStateStore(prevalidated_root).load() if resume else None
    if resume and preloaded_state is not None:
        _validate_state_binding(preloaded_state, config_binding)
        _validate_state_inputs(preloaded_state, input_files)
    if resume:
        _validate_resume_artifacts(
            root_dir=prevalidated_root,
            plan=plan,
            state=preloaded_state,
            checkpoint_index=CheckpointManager(prevalidated_root).load(),
        )

    runtime = initialize_runtime_context(
        work_dir=work_dir,
        config_file=config_file,
        input_files=input_files,
        original_inputs=original_inputs,
        resume=resume,
        logger=logger,
        global_config=global_config,
    )

    root_dir = runtime.root_dir
    checkpoint = runtime.checkpoint
    stats_tracker = runtime.stats_tracker
    failure_tracker = runtime.failure_tracker
    resume_from_step = runtime.resume_from_step
    current_input = runtime.current_input
    initial_input = list(current_input) if isinstance(current_input, list) else current_input
    step_outputs: dict[str, str | list[str]] = {}
    stats_tracker.stats["initial_conformers"] = count_conformers_any(current_input)

    state_store = WorkflowStateStore(root_dir)
    state = preloaded_state if resume else None
    if state is None:
        state = _initial_workflow_state(
            root_dir=root_dir,
            input_files=input_files,
            original_inputs=original_inputs,
            config_file=config_file,
            steps=steps,
            step_dirnames=step_dirnames,
            config_binding=config_binding,
            input_digests=_compute_input_digests(input_files),
        )
        state_store.save(state)
    else:
        _validate_state_steps(state, steps, step_dirnames)

    # === Print workflow start header ===
    print_workflow_start(input_files, current_input)

    def _resolve_inputs_for_step(step_name: str) -> str | list[str]:
        predecessors_for_step = predecessors[step_name]
        if not predecessors_for_step:
            return list(initial_input) if isinstance(initial_input, list) else initial_input

        outputs: list[str] = []
        for predecessor in predecessors_for_step:
            output = step_outputs.get(predecessor)
            if output is None:
                raise RuntimeError(
                    f"step {step_name!r} depends on {predecessor!r} but that step produced no output"
                )
            if isinstance(output, list):
                outputs.extend(output)
            else:
                outputs.append(output)
        return outputs[0] if len(outputs) == 1 else outputs

    for execution_index, step_name in enumerate(execution_order):
        step = by_step_name[step_name]
        step_dirname = name_to_dirname[step_name]
        step_dir = os.path.join(root_dir, step_dirname)
        state_record = state.steps[step_dirname]
        if resume and state_record.status in {"completed", "skipped"}:
            if state_record.output_xyz is None:
                if state_record.status == "skipped" and not step.get("enabled", True):
                    try:
                        step_outputs[step_name] = _resolve_inputs_for_step(step_name)
                    except RuntimeError:
                        step_outputs[step_name] = current_input
                    continue
                raise RuntimeError(
                    _resume_failure_message(
                        step_index=execution_index + 1,
                        step_name=state_record.name,
                        step_dir=step_dir,
                        reason="workflow state has no output path",
                    )
                )
            try:
                reusable_output = validate_reusable_artifact(
                    output_path=state_record.output_xyz,
                    root_dir=root_dir,
                    step_dir=step_dir,
                    step_name=step_name,
                    step_type=str(step.get("type", "")),
                    current_input=_resolve_inputs_for_step(step_name),
                    input_files=input_files,
                    params=step.get("params", {}) or {},
                    global_config=global_config,
                    typed_global=typed_global,
                    steps=steps,
                )
            except (ResumeArtifactCompatibilityError, OSError, ValueError, TypeError) as exc:
                raise RuntimeError(
                    _resume_failure_message(
                        step_index=execution_index + 1,
                        step_name=state_record.name,
                        step_dir=step_dir,
                        reason=str(exc),
                    )
                ) from exc
            step_outputs[step_name] = reusable_output
            current_input = reusable_output
            continue

        if resume_from_step >= execution_index:
            if not step.get("enabled", True):
                try:
                    step_outputs[step_name] = _resolve_inputs_for_step(step_name)
                except RuntimeError:
                    step_outputs[step_name] = current_input
                continue

            inputs_for_step = _resolve_inputs_for_step(step_name)
            if step.get("type") in ["calc", "task"]:
                params = step.get("params", {}) or {}
                calc_config = resolve_calc_step(
                    params,
                    typed_global,
                    input_chk_dir=_resolve_chk_input_dir(params, root_dir, steps),
                )
                input_for_digest = (
                    inputs_for_step if isinstance(inputs_for_step, str) else inputs_for_step[0]
                )
                manager = CalcArtifactManager(
                    step_dir,
                    step_name=step_name,
                    config=calc_config,
                    input_path=input_for_digest,
                )
                try:
                    prepared = manager.prepare(resume=True)
                except CalcResumeCompatibilityError as exc:
                    raise RuntimeError(
                        _resume_failure_message(
                            step_index=execution_index + 1,
                            step_name=step_name,
                            step_dir=step_dir,
                            reason=str(exc),
                        )
                    ) from exc
                if prepared.reusable_output is not None:
                    try:
                        current_input = validate_reusable_artifact(
                            output_path=str(prepared.reusable_output),
                            root_dir=root_dir,
                            step_dir=step_dir,
                            step_name=step_name,
                            step_type=str(step.get("type", "")),
                            current_input=inputs_for_step,
                            input_files=input_files,
                            params=params,
                            global_config=global_config,
                            typed_global=typed_global,
                            steps=steps,
                        )
                    except (
                        ResumeArtifactCompatibilityError,
                        OSError,
                        ValueError,
                        TypeError,
                    ) as exc:
                        raise RuntimeError(
                            _resume_failure_message(
                                step_index=execution_index + 1,
                                step_name=step_name,
                                step_dir=step_dir,
                                reason=str(exc),
                            )
                        ) from exc
                    step_outputs[step_name] = current_input
                    _mark_step_completed(state, state_record, current_input, execution_index)
                    state_store.save(state)
                    continue
                # A manifest that is present but not completed means the previous
                # run crashed mid-step; a residual output on disk must never be
                # adopted as a finished result.
                _reject_incomplete_manifest(
                    prepared,
                    step_index=execution_index + 1,
                    step_name=step_name,
                    step_dir=step_dir,
                )

            expected_output = resolve_step_output(step_dir, step.get("type"))
            if expected_output is not None and os.path.exists(expected_output):
                try:
                    current_input = validate_reusable_artifact(
                        output_path=expected_output,
                        root_dir=root_dir,
                        step_dir=step_dir,
                        step_name=step_name,
                        step_type=str(step.get("type", "")),
                        current_input=inputs_for_step,
                        input_files=input_files,
                        params=step.get("params", {}) or {},
                        global_config=global_config,
                        typed_global=typed_global,
                        steps=steps,
                    )
                except (ResumeArtifactCompatibilityError, OSError, ValueError, TypeError) as exc:
                    raise RuntimeError(
                        _resume_failure_message(
                            step_index=execution_index + 1,
                            step_name=step_name,
                            step_dir=step_dir,
                            reason=str(exc),
                        )
                    ) from exc
                step_outputs[step_name] = current_input
                _mark_step_completed(state, state_record, current_input, execution_index)
                state_store.save(state)
                continue

            raise RuntimeError(
                _resume_failure_message(
                    step_index=execution_index + 1,
                    step_name=step_name,
                    step_dir=step_dir,
                    reason=_expected_output_reason(step.get("type")),
                )
            )

        # Cancellation is distinct from a resumable pause and wins at boundaries.
        if cancel_beacon_file and os.path.exists(cancel_beacon_file):
            raise StopRequestedError(f"Cancel beacon found at {cancel_beacon_file}")

        # Check pause beacon before executing new step
        if pause_beacon_file and os.path.exists(pause_beacon_file):
            raise StopRequestedError(f"Pause beacon found at {pause_beacon_file}")

        if not step.get("enabled", True):
            try:
                step_outputs[step_name] = _resolve_inputs_for_step(step_name)
            except RuntimeError:
                step_outputs[step_name] = current_input
            state_record.status = "skipped"
            _notify_step_status_change(on_step_status_change, state_record)
            state_store.save(state)
            continue

        step_type = step["type"]
        os.makedirs(step_dir, exist_ok=True)

        step_start = time.time()
        inputs_for_step = _resolve_inputs_for_step(step_name)
        current_input = inputs_for_step
        in_n = count_conformers_any(inputs_for_step)

        step_stats = {
            "name": step_name,
            "type": step_type,
            "index": execution_index + 1,
            "input_conformers": in_n,
            "start_time": datetime.now().isoformat(),
        }

        params = step.get("params", {}) or {}

        # Notify server of the current step_dir for STOP beacon injection
        if step_started_callback:
            step_started_callback(step_name, step_type, step_dir)

        state_record.status = "submitted"
        state_record.submitted_at = time.time()
        state_record.error = None
        state.wavefront_index = execution_index
        state_store.save(state)
        _notify_step_status_change(on_step_status_change, state_record)

        # === Step header ===
        total_steps = len(steps)
        print_step_header_block(
            step_index=execution_index + 1,
            total_steps=total_steps,
            step_name=step_name,
            step_type=step_type,
            global_config=global_config,
            params=params,
            in_count=in_n,
        )

        try:
            if step_type in ["confgen", "gen"]:
                step_result = _run_confgen_step(
                    step_dir,
                    inputs_for_step,
                    params,
                    input_files,
                    global_config,
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
                step_result = _run_calc_step(
                    step_dir,
                    inputs_for_step,
                    params,
                    global_config,
                    root_dir,
                    steps,
                    failure_tracker,
                    step_name,
                    typed_global=typed_global,
                    **({"calc_executor": calc_executor} if calc_executor is not None else {}),
                    **(
                        {"cancel_beacon_file": cancel_beacon_file}
                        if cancel_beacon_file is not None
                        else {}
                    ),
                )
                current_input = step_result.output_path
                io_xyz.ensure_xyz_cids(current_input, prefix=index_to_letter_prefix(0))
                if step_result.reused_existing:
                    step_stats["status"] = TaskStatus.SKIPPED
                else:
                    step_stats["status"] = TaskStatus.COMPLETED

            step_outputs[step_name] = current_input
            step_stats["output_xyz"] = (
                os.path.abspath(current_input) if isinstance(current_input, str) else current_input
            )

        except Exception as e:
            # noqa: BLE001 - dispatcher-level: any step failure must mark FAILED + checkpoint + re-raise for the engine to abort the pipeline
            step_stats["status"] = TaskStatus.FAILED
            step_stats["error"] = str(e)
            state_record.status = "failed"
            state_record.error = str(e)
            state_record.fail_count += 1
            state_record.completed_at = time.time()
            state.final_status = "failed"
            state_store.save(state)
            _notify_step_status_change(on_step_status_change, state_record)
            checkpoint.save(execution_index - 1, stats_tracker.get_stats())
            raise
        finally:
            step_stats["end_time"] = datetime.now().isoformat()
            step_stats["duration_seconds"] = round(time.time() - step_start, 2)
            step_stats["output_conformers"] = count_conformers_any(current_input)

            failed_count = 0
            if step_type in ["calc", "task"]:
                db_path = os.path.join(step_dir, "results.db")
                failed_count = TaskStatsCollector.count_failed(db_path) or 0
                step_stats["failed_conformers"] = failed_count

            # === Step footer summary ===
            print_step_footer_block(
                step_stats=step_stats,
                in_count=in_n,
                failed_count=failed_count,
            )

            stats_tracker.add_step(step_stats)
            if step_stats["status"] in [
                TaskStatus.COMPLETED,
                TaskStatus.SKIPPED,
                TaskStatus.SKIPPED_MULTI,
            ]:
                checkpoint.save(execution_index, stats_tracker.get_stats())

                _mark_step_completed(state, state_record, current_input, execution_index)
                state_store.save(state)
                _notify_step_status_change(on_step_status_change, state_record)

    # A disabled node passes its effective input through.  That input can be
    # an upstream generated artifact (which remains a valid terminal result),
    # or an original input outside the workflow root (which must stay out of
    # the output manifest).  Determine this from the paths rather than from
    # ``status == skipped`` so ``source -> disabled sink`` still returns the
    # source artifact.
    terminal_outputs: dict[str, list[str]] = {}
    for name in terminal_steps:
        output = step_outputs.get(name)
        record = state.steps[name_to_dirname[name]]
        artifacts = (
            _workflow_artifact_list(output, root_dir, initial_input)
            if record.status == "skipped"
            else _as_artifact_list(output or [])
        )
        if artifacts:
            terminal_outputs[name] = artifacts
    outputting_terminals = [name for name in terminal_steps if name in terminal_outputs]
    final_outputs = [artifact for artifacts in terminal_outputs.values() for artifact in artifacts]
    if len(outputting_terminals) == 1:
        final_output = terminal_outputs[outputting_terminals[0]]
        if len(final_output) == 1:
            final_output = final_output[0]
    elif outputting_terminals:
        final_output = final_outputs
    else:
        final_output = initial_input
    return finalize_workflow(
        root_dir=root_dir,
        original_inputs=original_inputs,
        final_output=final_output,
        terminal_outputs=terminal_outputs,
        execution_count=len(execution_order),
        state=state,
        state_store=state_store,
        stats_tracker=stats_tracker,
        logger=logger,
    )


def finalize_workflow(
    *,
    root_dir: str,
    original_inputs: list[str],
    final_output: str | list[str],
    terminal_outputs: dict[str, list[str]],
    execution_count: int,
    state: WorkflowState,
    state_store: WorkflowStateStore,
    stats_tracker: Any,
    logger: Any,
) -> dict[str, Any]:
    """Engine compatibility adapter for the extracted finalization boundary."""
    return _finalize_workflow_impl(
        root_dir=root_dir,
        original_inputs=original_inputs,
        final_output=final_output,
        terminal_outputs=terminal_outputs,
        execution_count=execution_count,
        state=state,
        state_store=state_store,
        stats_tracker=stats_tracker,
        logger=logger,
        trace_low_energy=Tracer.trace_low_energy,
        emit_final_report_and_lowest=emit_final_report_and_lowest,
        write_final_statistics=write_final_statistics,
    )


def _resolve_resume_inputs(
    step_name: str,
    predecessors: dict[str, list[str]],
    outputs: dict[str, str | list[str]],
    initial_input: str | list[str],
) -> str | list[str]:
    """Resolve a saved step input without consulting mutable runtime state."""
    predecessor_names = predecessors[step_name]
    if not predecessor_names:
        return list(initial_input) if isinstance(initial_input, list) else initial_input

    resolved: list[str] = []
    for predecessor in predecessor_names:
        output = outputs.get(predecessor)
        if output is None:
            raise RuntimeError(
                f"workflow state has no reusable output for predecessor {predecessor!r}"
            )
        if isinstance(output, list):
            resolved.extend(output)
        else:
            resolved.append(output)
    return resolved[0] if len(resolved) == 1 else resolved


def _validate_resume_artifacts(
    *,
    root_dir: str,
    plan: Any,
    state: WorkflowState | None,
    checkpoint_index: int,
) -> None:
    """Validate every artifact strict resume may adopt before runtime setup."""
    initial_input: str | list[str] = (
        plan.input_files[0] if len(plan.input_files) == 1 else list(plan.input_files)
    )
    saved_outputs: dict[str, str | list[str]] = {}

    for execution_index, step_name in enumerate(plan.execution_order):
        step = plan.by_step_name[step_name]
        dirname = plan.name_to_dirname[step_name]
        step_dir = os.path.join(root_dir, dirname)
        record = state.steps[dirname] if state is not None else None

        try:
            current_input = _resolve_resume_inputs(
                step_name,
                plan.predecessors,
                saved_outputs,
                initial_input,
            )
        except RuntimeError as exc:
            # A later checkpoint cannot be trusted if one of its predecessor
            # outputs is absent from the saved state.
            if checkpoint_index >= execution_index:
                raise RuntimeError(
                    _resume_failure_message(
                        step_index=execution_index + 1,
                        step_name=step_name,
                        step_dir=step_dir,
                        reason=str(exc),
                    )
                ) from exc
            current_input = initial_input

        output_path: str | None = None
        if record is not None and record.status == "completed":
            output_path = record.output_xyz
            if output_path is None:
                raise RuntimeError(
                    _resume_failure_message(
                        step_index=execution_index + 1,
                        step_name=record.name,
                        step_dir=step_dir,
                        reason="workflow state has no output path",
                    )
                )
        elif record is not None and record.status == "skipped":
            # A disabled step normally has no own artifact and simply forwards
            # its input.  If a malformed state claims one, validate it rather
            # than silently discarding the claim.
            if record.output_xyz is not None:
                output_path = record.output_xyz
            else:
                saved_outputs[step_name] = current_input
                continue
        elif checkpoint_index >= execution_index:
            output_path = resolve_step_output(step_dir, step.get("type"))
            if output_path is None:
                raise RuntimeError(
                    _resume_failure_message(
                        step_index=execution_index + 1,
                        step_name=step_name,
                        step_dir=step_dir,
                        reason=_expected_output_reason(step.get("type")),
                    )
                )
        else:
            continue

        try:
            validated = validate_reusable_artifact(
                output_path=output_path,
                root_dir=root_dir,
                step_dir=step_dir,
                step_name=step_name,
                step_type=str(step.get("type", "")),
                current_input=current_input,
                input_files=plan.input_files,
                params=step.get("params", {}) or {},
                global_config=plan.global_config,
                typed_global=plan.typed_global,
                steps=plan.steps,
            )
        except (ResumeArtifactCompatibilityError, OSError, ValueError, TypeError) as exc:
            raise RuntimeError(
                _resume_failure_message(
                    step_index=execution_index + 1,
                    step_name=step_name,
                    step_dir=step_dir,
                    reason=str(exc),
                )
            ) from exc
        saved_outputs[step_name] = validated


def _initial_workflow_state(
    *,
    root_dir: str,
    input_files: list[str],
    original_inputs: list[str],
    config_file: str,
    steps: list[dict[str, Any]],
    step_dirnames: list[str],
    config_binding: Any,
    input_digests: list[str],
) -> WorkflowState:
    """Create state records keyed by the deterministic step directory names."""
    records = {
        dirname: StepRecord(
            name=str(step.get("name", dirname)),
            type=str(step.get("type", "")),
            status="skipped" if not step.get("enabled", True) else "pending",
        )
        for dirname, step in zip(step_dirnames, steps, strict=True)
    }
    return WorkflowState(
        run_id=str(uuid.uuid4()),
        work_dir=root_dir,
        input_files=input_files,
        original_inputs=original_inputs,
        config_file=os.path.abspath(config_file),
        config_binding=config_binding,
        input_digests=input_digests,
        steps=records,
    )


def _as_artifact_list(output: str | list[str]) -> list[str]:
    """Normalize a terminal step output to absolute artifact paths."""
    if isinstance(output, str):
        return [os.path.abspath(output)]
    if isinstance(output, list):
        return [os.path.abspath(path) for path in output if isinstance(path, str)]
    return []


def _workflow_artifact_list(
    output: str | list[str] | None,
    root_dir: str,
    external_inputs: str | list[str],
) -> list[str]:
    """Return output paths that are files contained by the workflow root.

    Disabled steps may forward an external original input.  Such a path is a
    valid execution input but is not an artifact produced by this workflow and
    therefore cannot be advertised by ``output_manifest.json``.
    """
    root = os.path.realpath(root_dir)
    original_paths = {os.path.realpath(path) for path in _as_artifact_list(external_inputs)}
    artifacts: list[str] = []
    for path in _as_artifact_list(output or []):
        resolved = os.path.realpath(path)
        try:
            contained = os.path.commonpath((root, resolved)) == root
        except ValueError:
            contained = False
        if (
            contained
            and resolved != root
            and resolved not in original_paths
            and os.path.isfile(path)
        ):
            artifacts.append(os.path.abspath(path))
    return artifacts


def _reject_incomplete_manifest(
    prepared: PreparedCalcArtifacts,
    *,
    step_index: int,
    step_name: str,
    step_dir: str,
) -> None:
    """Refuse to adopt a residual output when the calc manifest is not completed."""
    manifest = prepared.manifest
    if manifest is None or manifest.status == "completed":
        return
    raise RuntimeError(
        _resume_failure_message(
            step_index=step_index,
            step_name=step_name,
            step_dir=step_dir,
            reason=(
                f"calc manifest is not complete (status={manifest.status}); "
                "refusing to reuse a residual output"
            ),
        )
    )


def _compute_input_digests(input_files: list[str]) -> list[str]:
    """Ordered content signatures (absolute path + content digest) for resume."""
    signatures: list[str] = []
    for path in input_files:
        abspath = os.path.abspath(path)
        signatures.append(f"{abspath}:{compute_input_digest(abspath)}")
    return signatures


def _validate_state_inputs(state: WorkflowState, input_files: list[str]) -> None:
    """Reject strict resume when the original inputs are missing or changed."""
    if not state.input_digests:
        raise RuntimeError(
            "Workflow state has no input binding; strict resume cannot verify the "
            "original inputs. Re-run without --resume."
        )
    try:
        current = _compute_input_digests(input_files)
    except OSError as exc:
        raise RuntimeError(f"Strict resume cannot read the original inputs: {exc}") from exc
    if current != state.input_digests:
        raise RuntimeError(
            "Workflow state input files do not match the current inputs "
            "(path or content changed); strict resume refuses artifact reuse or cleanup."
        )


def _validate_state_binding(state: WorkflowState, expected: Any) -> None:
    """Reject legacy, malformed, unknown, or mismatched resume bindings."""
    saved = state.config_binding
    if saved is None:
        raise RuntimeError(
            "Workflow state has no config binding; legacy/unschematized state "
            "may be inspected but cannot be resumed."
        )
    if saved.to_dict() != expected.to_dict():
        raise RuntimeError(
            "Workflow state config binding does not match the configured workflow; "
            "strict resume refuses artifact reuse or cleanup."
        )


def _validate_state_steps(
    state: WorkflowState,
    steps: list[dict[str, Any]],
    step_dirnames: list[str],
) -> None:
    """Reject resume when the saved graph no longer matches the configuration."""
    expected = set(step_dirnames)
    if set(state.steps) != expected:
        raise RuntimeError("Workflow state does not match the configured workflow steps")
    for dirname, step in zip(step_dirnames, steps, strict=True):
        record = state.steps[dirname]
        if record.name != str(step.get("name", dirname)) or record.type != str(
            step.get("type", "")
        ):
            raise RuntimeError("Workflow state does not match the configured workflow steps")


def _mark_step_completed(
    state: WorkflowState,
    record: StepRecord,
    current_input: str | list[str],
    index: int,
) -> None:
    record.status = "completed"
    record.completed_at = time.time()
    record.output_xyz = current_input if isinstance(current_input, str) else None
    record.error = None
    state.wavefront_index = index + 1


def _notify_step_status_change(
    callback: Callable[[StepRecord], None] | None,
    record: StepRecord,
) -> None:
    if callback:
        callback(record)
