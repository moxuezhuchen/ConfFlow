#!/usr/bin/env python3

"""Workflow step handler functions."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from typing import Any

from .. import calc
from ..blocks import confgen
from ..calc.step_contract import (
    compute_calc_input_signature,
    prepare_calc_step_dir,
    record_calc_step_signature,
)
from ..config.schema import ConfigSchema
from ..core.exceptions import ConfFlowError
from ..core.pairs import normalize_pair_list
from ..core.utils import get_logger
from .config_builder import build_task_config
from .helpers import as_list, is_multi_frame_any, pushd, resolve_step_output
from .stats import FailureTracker

__all__ = [
    "StepContext",
    "run_confgen_step",
    "run_calc_step",
]

logger = get_logger()


@dataclass
class StepContext:
    """Encapsulates common parameters shared between step handler functions.

    Reduces the parameter count of ``run_calc_step`` from 8 positional
    arguments to a single context object, improving readability and
    making it easier to add new context fields in the future.
    """

    step_dir: str
    current_input: str | list[str]
    params: dict[str, Any]
    global_config: dict[str, Any] = field(default_factory=dict)
    root_dir: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    failure_tracker: FailureTracker | None = None
    step_name: str = ""


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
                collect_results=False,
            )
        if not os.path.exists(expected_output):
            raise ConfFlowError("confgen did not produce search.xyz")
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
    input_signature = compute_calc_input_signature(current_input)
    input_source = current_input if isinstance(current_input, str) else current_input[0]

    prepared = prepare_calc_step_dir(step_dir, task_config, input_signature=input_signature)
    if prepared.cleaned_stale_artifacts:
        logger.warning(
            "Discarding stale calc artifacts in '%s' because the step state is incomplete or outdated.",
            step_dir,
        )
    if prepared.reusable_output is not None:
        if prepared.state.failed_path is not None:
            failure_tracker.append(prepared.state.failed_path, step_name)
        return prepared.reusable_output

    if isinstance(current_input, list) and len(current_input) > 1:
        logger.warning(
            "Calc step received %d input files; using only '%s'. "
            "Add a confgen step to merge multiple inputs before calc.",
            len(current_input),
            current_input[0],
        )

    manager = calc.ChemTaskManager(task_config)
    manager.work_dir = step_dir
    manager._input_signature_override = input_signature
    record_calc_step_signature(step_dir, task_config, input_signature=input_signature)
    manager.run(
        input_xyz_file=input_source
    )

    work_failed = os.path.join(step_dir, "failed.xyz")

    final_input = resolve_step_output(step_dir, "calc")
    if final_input is None:
        raise ConfFlowError("Calculation step did not produce an output XYZ file")

    if os.path.exists(work_failed):
        failure_tracker.append(work_failed, step_name)

    return final_input
