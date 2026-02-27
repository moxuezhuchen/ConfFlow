#!/usr/bin/env python3

"""Workflow step handler functions."""

from __future__ import annotations

import os
import shutil
from typing import Any

from .. import calc
from ..blocks import confgen
from ..config.schema import ConfigSchema
from ..core.exceptions import ConfFlowError
from ..core.pairs import normalize_pair_list
from .config_builder import build_task_config
from .helpers import as_list, is_multi_frame_any, pushd, resolve_step_output
from .stats import FailureTracker

__all__ = [
    "run_confgen_step",
    "run_calc_step",
]


def run_confgen_step(
    step_dir: str,
    current_input: str | list[str],
    params: dict[str, Any],
    input_files: list[str],
) -> str:
    """Execute a conformer generation step (execution adapter layer)."""
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
            raise ConfFlowError("confgen did not generate search.xyz")
    return expected_output


def run_calc_step(
    step_dir: str,
    current_input: str | list[str],
    params: dict[str, Any],
    global_config: dict[str, Any],
    root_dir: str,
    steps: list[dict[str, Any]],
    failure_tracker: FailureTracker,
    step_name: str,
) -> str:
    """Execute a calculation step (execution adapter layer)."""
    task_config = build_task_config(params, global_config, root_dir, steps)
    ConfigSchema.validate_calc_config(task_config)

    existing_output = resolve_step_output(step_dir, "calc")
    if existing_output is not None:
        final_input = existing_output
        step_failed = os.path.join(step_dir, "failed.xyz")
        if os.path.exists(step_failed):
            failure_tracker.append(step_failed, step_name)
        return final_input

    manager = calc.ChemTaskManager(task_config)
    manager.work_dir = step_dir
    manager.run(
        input_xyz_file=current_input if isinstance(current_input, str) else current_input[0]
    )

    work_failed = os.path.join(step_dir, "failed.xyz")

    final_input = resolve_step_output(step_dir, "calc")
    if final_input is None:
        raise ConfFlowError("Calculation task did not produce expected output")

    if os.path.exists(work_failed):
        failure_tracker.append(work_failed, step_name)

    return final_input
