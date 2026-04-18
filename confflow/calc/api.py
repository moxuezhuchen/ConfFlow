#!/usr/bin/env python3

"""Official workflow-facing calc entrypoints."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .step_contract import (
    compute_calc_input_signature,
    prepare_calc_step_dir,
    record_calc_step_signature,
)

__all__ = [
    "CalcStepExecutionResult",
    "run_calc_workflow_step",
]


@dataclass(frozen=True)
class CalcStepExecutionResult:
    """Stable return value for workflow -> calc step execution."""

    output_path: str
    reused_existing: bool = False
    cleaned_stale_artifacts: bool = False
    failed_path: str | None = None


def run_calc_workflow_step(
    *,
    step_dir: str,
    input_source: str | list[str],
    legacy_task_config: dict[str, Any],
    execution_config: dict[str, Any] | Any,
) -> CalcStepExecutionResult:
    """Run one calc workflow step through the recommended calc facade.

    This is the preferred workflow-facing entrypoint. It keeps workflow callers
    out of `ChemTaskManager` lifecycle details while preserving the existing
    compat/signature/resume contract implemented by `step_contract`.
    """
    input_signature = compute_calc_input_signature(input_source)
    input_path = input_source if isinstance(input_source, str) else input_source[0]

    prepared = prepare_calc_step_dir(
        step_dir,
        legacy_task_config,
        input_signature=input_signature,
        execution_config=execution_config,
    )
    if prepared.reusable_output is not None:
        return CalcStepExecutionResult(
            output_path=prepared.reusable_output,
            reused_existing=True,
            cleaned_stale_artifacts=prepared.cleaned_stale_artifacts,
            failed_path=prepared.state.failed_path,
        )

    from . import ChemTaskManager

    manager = ChemTaskManager(
        settings=legacy_task_config,
        execution_config=execution_config,
    )
    manager.work_dir = step_dir
    manager._input_signature_override = input_signature

    # Keep pre-run signature recording for compatibility with partial resume
    # state and mocked manager-based tests; manager.run() still records again.
    record_calc_step_signature(
        step_dir,
        legacy_task_config,
        input_signature=input_signature,
        execution_config=execution_config,
    )

    manager.run(input_xyz_file=input_path)

    output_path = None
    for name in ("output.xyz", "result.xyz"):
        candidate = os.path.join(step_dir, name)
        if os.path.exists(candidate):
            output_path = candidate
            break
    if output_path is None:
        raise RuntimeError("Calculation step did not produce an output XYZ file")

    failed_path = os.path.join(step_dir, "failed.xyz")
    return CalcStepExecutionResult(
        output_path=output_path,
        reused_existing=False,
        cleaned_stale_artifacts=prepared.cleaned_stale_artifacts,
        failed_path=failed_path if os.path.exists(failed_path) else None,
    )
