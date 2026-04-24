#!/usr/bin/env python3

"""Run the workflow without calling ``sys.exit`` directly."""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any

from ..calc.step_contract import (
    compute_calc_input_signature,
    prepare_calc_step_dir,
)
from ..config.schema import ConfigSchema
from ..core import io as io_xyz
from ..core.types import TaskStatus
from ..core.utils import (
    get_logger,
    index_to_letter_prefix,
    validate_xyz_file,
)
from .config_builder import build_task_config, load_workflow_config
from .helpers import count_conformers_any, is_multi_frame_any, resolve_step_output
from .presenter import (
    emit_final_report_and_lowest,
    print_step_footer_block,
    print_step_header_block,
    print_workflow_start,
    write_final_statistics,
)
from .runtime_context import initialize_runtime_context
from .stats import (
    FailureTracker,
    TaskStatsCollector,
    Tracer,
)
from .step_handlers import run_calc_step as step_run_calc_step
from .step_handlers import run_confgen_step as step_run_confgen_step
from .step_naming import build_step_dir_name_map
from .validation import validate_inputs_compatible

__all__ = [
    "run_workflow",
]

logger = get_logger()


def _run_confgen_step(
    step_dir: str, current_input: str | list[str], params: dict[str, Any], input_files: list[str]
) -> str:
    """Execute a conformer generation step."""
    return step_run_confgen_step(step_dir, current_input, params, input_files)


def _run_calc_step(
    step_dir: str,
    current_input: str | list[str],
    params: dict[str, Any],
    global_config: dict[str, Any],
    root_dir: str,
    steps: list[dict[str, Any]],
    failure_tracker: FailureTracker,
    step_name: str,
) -> str:
    """Execute a calculation task step."""
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
) -> dict[str, Any]:
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

    cfg = load_workflow_config(config_file)
    global_config = cfg["global"]
    steps = cfg["steps"]
    step_dirnames, _ = build_step_dir_name_map(steps)

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
    stats_tracker.stats["initial_conformers"] = count_conformers_any(current_input)

    # === Print workflow start header ===
    print_workflow_start(input_files, current_input)

    for i, step in enumerate(steps):
        if resume_from_step >= i:
            if not step.get("enabled", True):
                continue

            # If resuming and this step is already completed, update current_input to its output
            step_dir = os.path.join(root_dir, step_dirnames[i])
            if step.get("type") in ["calc", "task"]:
                params = step.get("params", {}) or {}
                task_config = build_task_config(params, global_config, root_dir, steps)
                ConfigSchema.validate_calc_config(task_config)
                prepared = prepare_calc_step_dir(
                    step_dir,
                    task_config,
                    input_signature=compute_calc_input_signature(current_input),
                )
                if prepared.reusable_output is not None:
                    current_input = prepared.reusable_output
                    continue
                if prepared.state.has_resume_state:
                    raise RuntimeError(
                        "Resume failed: calc step "
                        f"{i + 1} ('{step_dirnames[i]}') is incomplete or stale in {step_dir}. "
                        "The step directory was cleaned; rerun from this step without relying on the old checkpoint."
                    )

            expected_output = resolve_step_output(step_dir, step.get("type"))
            if expected_output is not None and os.path.exists(expected_output):
                current_input = expected_output
                continue

            raise RuntimeError(
                "Resume failed: step "
                f"{i + 1} ('{step_dirnames[i]}') output not found in {step_dir}. "
                "The working directory is incomplete; re-run from this step or remove resume."
            )

        if not step.get("enabled", True):
            continue

        step_name = step["name"]
        step_type = step["type"]
        step_dir = os.path.join(root_dir, step_dirnames[i])
        os.makedirs(step_dir, exist_ok=True)

        step_start = time.time()
        in_n = count_conformers_any(current_input)

        step_stats = {
            "name": step_name,
            "type": step_type,
            "index": i + 1,
            "input_conformers": in_n,
            "start_time": datetime.now().isoformat(),
        }

        params = step.get("params", {}) or {}

        # === Step header ===
        total_steps = len(steps)
        print_step_header_block(
            step_index=i + 1,
            total_steps=total_steps,
            step_name=step_name,
            step_type=step_type,
            global_config=global_config,
            params=params,
            in_count=in_n,
        )

        try:
            if step_type in ["confgen", "gen"]:
                multi_frame = len(input_files) == 1 and is_multi_frame_any(current_input)
                expected_output = os.path.join(step_dir, "search.xyz")

                if multi_frame and isinstance(current_input, str):
                    step_stats["status"] = TaskStatus.SKIPPED_MULTI
                elif os.path.exists(expected_output):
                    step_stats["status"] = TaskStatus.SKIPPED

                current_input = _run_confgen_step(step_dir, current_input, params, input_files)
                io_xyz.ensure_xyz_cids(current_input, prefix=index_to_letter_prefix(0))
                if step_stats.get("status") not in [TaskStatus.SKIPPED_MULTI, TaskStatus.SKIPPED]:
                    step_stats["status"] = TaskStatus.COMPLETED

            elif step_type in ["calc", "task"]:
                current_input = _run_calc_step(
                    step_dir,
                    current_input,
                    params,
                    global_config,
                    root_dir,
                    steps,
                    failure_tracker,
                    step_name,
                )
                io_xyz.ensure_xyz_cids(current_input, prefix=index_to_letter_prefix(0))
                if getattr(current_input, "reused_existing", False):
                    step_stats["status"] = TaskStatus.SKIPPED
                else:
                    step_stats["status"] = TaskStatus.COMPLETED

            if isinstance(current_input, list):
                step_stats["output_xyz"] = [os.path.abspath(p) for p in current_input]
            else:
                step_stats["output_xyz"] = os.path.abspath(current_input)

        except Exception as e:
            step_stats["status"] = TaskStatus.FAILED
            step_stats["error"] = str(e)
            checkpoint.save(i - 1, stats_tracker.get_stats())
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
                checkpoint.save(i, stats_tracker.get_stats())

    final_stats = stats_tracker.finalize(current_input)

    # Tracing
    try:
        Tracer.trace_low_energy(final_stats)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.debug(f"Trace failed: {e}")

    emit_final_report_and_lowest(current_input, original_inputs, final_stats, logger)
    write_final_statistics(root_dir, final_stats)

    return final_stats
