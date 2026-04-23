#!/usr/bin/env python3

"""Workflow step handler functions."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from typing import Any

from .. import calc
from ..blocks import confgen
from ..config.schema import ConfigSchema
from ..core.exceptions import ConfFlowError
from ..core.pairs import normalize_pair_list
from ..core.utils import get_logger
from .config_builder import build_task_config
from .helpers import as_list, is_multi_frame_any, pushd
from .stats import FailureTracker
from .task_config import build_structured_task_config

__all__ = [
    "StepContext",
    "CalcStepResult",
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


class CalcStepResult(str):
    """String-like calc step output path plus reuse metadata for engine callers."""

    reused_existing: bool
    cleaned_stale_artifacts: bool

    def __new__(
        cls,
        value: str,
        *,
        reused_existing: bool = False,
        cleaned_stale_artifacts: bool = False,
    ) -> CalcStepResult:
        obj = str.__new__(cls, value)
        obj.reused_existing = reused_existing
        obj.cleaned_stale_artifacts = cleaned_stale_artifacts
        return obj


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
    """Execute a calculation step via the calc package facade."""
    legacy_task_config = build_task_config(params, global_config, root_dir, steps)
    ConfigSchema.validate_calc_config(legacy_task_config)

    structured_task_config = build_structured_task_config(
        params,
        global_config,
        root_dir=root_dir,
        all_steps=steps,
    )

    try:
        result = calc.run_calc_workflow_step(
            step_dir=step_dir,
            input_source=current_input,
            legacy_task_config=legacy_task_config,
            execution_config=structured_task_config,
        )
    except RuntimeError as exc:
        if "did not produce an output XYZ file" in str(exc):
            raise ConfFlowError(str(exc)) from exc
        raise

    if result.cleaned_stale_artifacts:
        logger.warning(
            "Discarding stale calc artifacts in '%s' because the step state is incomplete or outdated.",
            step_dir,
        )
    if isinstance(current_input, list) and len(current_input) > 1:
        logger.warning(
            "Calc step received %d input files; using only '%s'. "
            "Add a confgen step to merge multiple inputs before calc.",
            len(current_input),
            current_input[0],
        )

    if result.failed_path is not None:
        failure_tracker.append(result.failed_path, step_name)

    if not os.path.exists(result.output_path):
        raise ConfFlowError("Calculation step did not produce an output XYZ file")

    return CalcStepResult(
        result.output_path,
        reused_existing=result.reused_existing,
        cleaned_stale_artifacts=result.cleaned_stale_artifacts,
    )
