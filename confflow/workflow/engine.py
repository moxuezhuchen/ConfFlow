#!/usr/bin/env python3

"""Run the workflow without calling ``sys.exit`` directly."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ..calc.artifacts import CalcArtifactManager
from ..calc.executor import CalcExecutor
from ..config.models import CalcStepParams, GlobalOptions, load_workflow_model
from ..core import io as io_xyz
from ..core.exceptions import StopRequestedError
from ..core.types import TaskStatus
from ..core.utils import (
    get_logger,
    index_to_letter_prefix,
    validate_xyz_file,
)
from .dag import build_step_graph, topo_order
from .helpers import count_conformers_any, resolve_step_output
from .presenter import (
    emit_final_report_and_lowest,
    print_step_footer_block,
    print_step_header_block,
    print_workflow_start,
    write_final_statistics,
)
from .runtime_context import initialize_runtime_context
from .state import StepRecord, WorkflowState, WorkflowStateStore
from .stats import (
    FailureTracker,
    TaskStatsCollector,
    Tracer,
)
from .step_handlers import StepExecutionResult
from .step_handlers import run_calc_step as step_run_calc_step
from .step_handlers import run_confgen_step as step_run_confgen_step
from .step_naming import build_step_dir_name_map
from .validation import validate_inputs_compatible

__all__ = [
    "run_workflow",
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
    calc_executor: CalcExecutor | None = None,
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
            calc_executor=calc_executor,
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

    input_files = [os.path.abspath(x) for x in input_xyz]
    original_inputs = (
        [os.path.abspath(x) for x in original_input_files] if original_input_files else input_files
    )
    for fp in input_files:
        if not os.path.exists(fp):
            raise FileNotFoundError(f"Input file does not exist: {fp}")
        validate_xyz_file(fp, strict=True)

    cfg = load_workflow_model(config_file).as_legacy_shape()
    global_config = cfg["global"]
    steps = cfg["steps"]

    # Explicit DAG mode is selected by the presence of an ``inputs`` field on
    # any step. In that mode, steps without the field are independent roots;
    # they do not inherit a predecessor from their list position. This keeps
    # mixed configurations deterministic and makes the legacy fallback
    # unambiguous: only a workflow with no ``inputs`` fields is linear.
    raw_predecessors, by_step_name, declared_inputs = build_step_graph(steps)
    explicit_inputs = any("inputs" in step for step in steps)
    if explicit_inputs:
        predecessors = raw_predecessors
    else:
        ordered_names = list(by_step_name)
        predecessors = {
            name: ([ordered_names[index - 1]] if index else [])
            for index, name in enumerate(ordered_names)
        }
    del declared_inputs
    execution_order = [name for wave in topo_order(predecessors) for name in wave]

    step_dirnames, _ = build_step_dir_name_map(steps)
    step_index_by_name = {name: index for index, name in enumerate(by_step_name)}
    name_to_dirname = {name: step_dirnames[index] for name, index in step_index_by_name.items()}

    # Pre-load confgen params for multi-input flexible chain consistency check
    confgen_params = None
    if len(input_files) > 1:
        for step in steps:
            if step.get("type", "").lower() == "confgen":
                confgen_params = step.get("params", {})
                break
        validate_inputs_compatible(
            input_files,
            confgen_params,
            force_consistency=global_config.get("force_consistency", False),
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
    state = state_store.load() if resume else None
    if state is None:
        state = _initial_workflow_state(
            root_dir=root_dir,
            input_files=input_files,
            original_inputs=original_inputs,
            config_file=config_file,
            steps=steps,
            step_dirnames=step_dirnames,
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
            if not os.path.exists(state_record.output_xyz):
                raise RuntimeError(
                    _resume_failure_message(
                        step_index=execution_index + 1,
                        step_name=state_record.name,
                        step_dir=step_dir,
                        reason=f"saved output is missing: {state_record.output_xyz}",
                    )
                )
            step_outputs[step_name] = state_record.output_xyz
            current_input = state_record.output_xyz
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
                typed_global = GlobalOptions.from_mapping(global_config)
                calc_config = CalcStepParams.from_params(params, typed_global)
                input_for_digest = (
                    inputs_for_step if isinstance(inputs_for_step, str) else inputs_for_step[0]
                )
                prepared = CalcArtifactManager(
                    step_dir,
                    step_name=step_name,
                    config=calc_config,
                    input_path=input_for_digest,
                ).prepare(resume=True)
                if prepared.reusable_output is not None:
                    current_input = str(prepared.reusable_output)
                    step_outputs[step_name] = current_input
                    _mark_step_completed(state, state_record, current_input, execution_index)
                    state_store.save(state)
                    continue
                if prepared.cleaned_stale_artifacts:
                    raise RuntimeError(
                        _resume_failure_message(
                            step_index=execution_index + 1,
                            step_name=step_name,
                            step_dir=step_dir,
                            reason="manifest digest did not match current config/input",
                        )
                    )

            expected_output = resolve_step_output(step_dir, step.get("type"))
            if expected_output is not None and os.path.exists(expected_output):
                current_input = expected_output
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
                    **({"calc_executor": calc_executor} if calc_executor is not None else {}),
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

    final_stats = stats_tracker.finalize(current_input)
    state.final_status = "completed"
    state.wavefront_index = len(execution_order)
    state_store.save(state)

    # Tracing
    try:
        Tracer.trace_low_energy(final_stats)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.debug(f"Trace failed: {e}")

    emit_final_report_and_lowest(current_input, original_inputs, final_stats, logger)
    write_final_statistics(root_dir, final_stats)

    return final_stats


def _initial_workflow_state(
    *,
    root_dir: str,
    input_files: list[str],
    original_inputs: list[str],
    config_file: str,
    steps: list[dict[str, Any]],
    step_dirnames: list[str],
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
        steps=records,
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
