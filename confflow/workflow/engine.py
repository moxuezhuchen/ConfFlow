#!/usr/bin/env python3

"""Workflow execution engine (split from confflow.main).

Design goals:
- Pure business logic: no sys.exit calls.
- Testable: core entry ``run_workflow()`` accepts explicit parameters.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from datetime import datetime
from typing import Any

from .. import calc
from ..blocks import confgen, viz
from ..config.schema import ConfigSchema
from ..core import io as io_xyz
from ..core.console import console

# Import extracted modules
from ..core.pairs import normalize_pair_list
from ..core.types import TaskStatus
from ..core.utils import (
    format_duration_hms,
    get_logger,
    index_to_letter_prefix,
)
from .config_builder import (
    build_step_dir_name_map,
    build_task_config,
    load_workflow_config,
)
from .config_builder import _itask_label as _itask_label  # re-export for test compatibility
from .config_builder import _normalize_iprog_label as _normalize_iprog_label  # re-export
from .helpers import as_list, count_conformers_any, is_multi_frame_any, pushd
from .presenter import print_step_footer_block, print_step_header_block, print_workflow_start
from .stats import (
    CheckpointManager,
    FailureTracker,
    TaskStatsCollector,
    Tracer,
    WorkflowStatsTracker,
)
from .validation import validate_inputs_compatible

__all__ = [
    "run_workflow",
]

logger = get_logger()


def _run_confgen_step(
    step_dir: str, current_input: str | list[str], params: dict[str, Any], input_files: list[str]
) -> str:
    """Execute a conformer generation step."""
    expected_output = os.path.join(step_dir, "search.xyz")
    multi_frame = len(input_files) == 1 and is_multi_frame_any(current_input)

    if multi_frame and isinstance(current_input, str):
        shutil.copy2(current_input, expected_output)
    elif not os.path.exists(expected_output):
        with pushd(step_dir):
            confgen.run_generation(
                input_files=current_input,
                angle_step=params.get("angle_step", 120),
                bond_threshold=params.get("bond_multiplier", 1.15),
                clash_threshold=0.65,
                add_bond=normalize_pair_list(params.get("add_bond")),
                del_bond=normalize_pair_list(params.get("del_bond")),
                no_rotate=normalize_pair_list(params.get("no_rotate")),
                force_rotate=normalize_pair_list(params.get("force_rotate")),
                optimize=params.get("optimize", False),
                confirm=False,
                chains=as_list(params.get("chains", params.get("chain"))),
                chain_steps=as_list(params.get("chain_steps", params.get("steps"))),
                chain_angles=as_list(params.get("chain_angles", params.get("angles"))),
                rotate_side=params.get("rotate_side", "left"),
            )
        if not os.path.exists(expected_output):
            raise RuntimeError("confgen did not generate search.xyz")
    return expected_output


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
    task_config = build_task_config(params, global_config, root_dir, steps)
    ConfigSchema.validate_calc_config(task_config)

    expected_clean = os.path.join(step_dir, "output.xyz")
    expected_raw = os.path.join(step_dir, "result.xyz")

    if os.path.exists(expected_clean) or os.path.exists(expected_raw):
        final_input = expected_clean if os.path.exists(expected_clean) else expected_raw
        step_failed = os.path.join(step_dir, "failed.xyz")
        if os.path.exists(step_failed):
            failure_tracker.append(step_failed, step_name)
        return final_input

    manager = calc.ChemTaskManager(task_config)
    manager.work_dir = step_dir
    manager.run(
        input_xyz_file=current_input if isinstance(current_input, str) else current_input[0]
    )

    work_cleaned = os.path.join(step_dir, "output.xyz")
    work_raw = os.path.join(step_dir, "result.xyz")
    work_failed = os.path.join(step_dir, "failed.xyz")

    if os.path.exists(work_cleaned):
        final_input = work_cleaned
    elif os.path.exists(work_raw):
        final_input = work_raw
    else:
        raise RuntimeError("Calculation task did not produce expected output")

    if os.path.exists(work_failed):
        failure_tracker.append(work_failed, step_name)

    return final_input


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

    root_dir = os.path.abspath(work_dir)
    os.makedirs(root_dir, exist_ok=True)
    failed_dir = os.path.join(root_dir, "failed")
    os.makedirs(failed_dir, exist_ok=True)

    try:
        shutil.copy2(config_file, os.path.join(failed_dir, os.path.basename(config_file)))
    except Exception:
        pass

    if hasattr(logger, "add_file_handler"):
        logger.add_file_handler(os.path.join(root_dir, "confflow.log"))

    checkpoint = CheckpointManager(root_dir)
    stats_tracker = WorkflowStatsTracker(input_files, original_inputs)
    failure_tracker = FailureTracker(failed_dir)

    if not resume:
        failure_tracker.clear_previous()

    resume_from_step = checkpoint.load() if resume else -1
    current_input: str | list[str] = input_files[0] if len(input_files) == 1 else input_files

    # === Print workflow start header ===
    print_workflow_start(input_files, current_input)

    for i, step in enumerate(steps):
        if resume_from_step >= i:
            # If resuming and this step is already completed, update current_input to its output
            step_dir = os.path.join(root_dir, step_dirnames[i])
            expected_output = os.path.join(step_dir, "output.xyz")
            if not os.path.exists(expected_output):
                expected_output = os.path.join(step_dir, "result.xyz")
            if not os.path.exists(expected_output):
                expected_output = os.path.join(step_dir, "search.xyz")
            if os.path.exists(expected_output):
                current_input = expected_output
            continue

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
                expected_clean = os.path.join(step_dir, "output.xyz")
                expected_raw = os.path.join(step_dir, "result.xyz")

                if os.path.exists(expected_clean) or os.path.exists(expected_raw):
                    step_stats["status"] = TaskStatus.SKIPPED

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
                if step_stats.get("status") != TaskStatus.SKIPPED:
                    step_stats["status"] = TaskStatus.COMPLETED

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
    except Exception as e:
        logger.debug(f"Trace failed: {e}")

    # Report and lowest energy output
    if isinstance(current_input, str) and os.path.exists(current_input):
        confs = viz.parse_xyz_file(current_input)
        report_text = viz.generate_text_report(confs, stats=final_stats)
        if report_text:
            # CLI redirects stdout to <input>.txt (isatty=False). When used as a library from a TTY,
            # keep silent by default.
            if not sys.stdout.isatty():
                print(report_text)

        best_conf, best_energy, _ = viz.get_lowest_energy_conformer(confs)
        if best_conf:
            input_dir = os.path.dirname(os.path.abspath(original_inputs[0]))
            input_base = os.path.splitext(os.path.basename(original_inputs[0]))[0]
            lowest_path = os.path.join(input_dir, f"{input_base}min.xyz")
            io_xyz.write_xyz_file(lowest_path, [best_conf], atomic=True)

            best_meta = best_conf.get("metadata") or {}
            final_stats["lowest_conformer"] = {
                "cid": best_meta.get("CID"),
                "energy": best_energy,
                "xyz_path": lowest_path,
            }
            logger.info(f"Lowest-energy conformer written: {lowest_path}")

    # Write final statistics
    stats_file = os.path.join(root_dir, "workflow_stats.json")
    with open(stats_file, "w", encoding="utf-8") as f:
        import json

        json.dump(final_stats, f, indent=2, ensure_ascii=False)

    return final_stats
