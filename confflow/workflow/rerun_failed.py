#!/usr/bin/env python3

"""Rerun failed conformers from an existing calc workflow step."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .. import calc
from ..config.schema import ConfigSchema
from ..core.exceptions import ConfigurationError
from ..core.io import read_xyz_file
from ..core.path_policy import resolve_sandbox_root, validate_managed_path
from .config_builder import build_task_config, load_workflow_config
from .task_config import build_structured_task_config

__all__ = [
    "RerunFailedResult",
    "RerunFailedUsageError",
    "RerunFailedRuntimeError",
    "run_rerun_failed",
]


class RerunFailedUsageError(ValueError):
    """Raised when rerun-failed CLI arguments are invalid."""


class RerunFailedRuntimeError(RuntimeError):
    """Raised when rerun-failed cannot complete at runtime."""


@dataclass(frozen=True)
class RerunFailedResult:
    """Summary for a rerun-failed execution."""

    failed_path: str
    config_file: str
    step_label: str
    output_dir: str
    input_count: int
    output_count: int
    failed_count: int


def _select_step(steps: list[dict[str, Any]], step_ref: str) -> tuple[int, dict[str, Any]]:
    ref = step_ref.strip()
    if not ref:
        raise RerunFailedUsageError("--step must not be empty")

    if ref.isdigit():
        index = int(ref)
        if 1 <= index <= len(steps):
            return index - 1, steps[index - 1]
        raise RerunFailedUsageError(f"--step index is out of range: {step_ref}")

    matches = [(idx, step) for idx, step in enumerate(steps) if str(step.get("name", "")) == ref]
    if not matches:
        raise RerunFailedUsageError(f"No workflow step named '{step_ref}' was found")
    if len(matches) > 1:
        raise RerunFailedUsageError(f"Workflow step name is ambiguous: {step_ref}")
    return matches[0]


def _read_conformer_count(path: str, *, label: str) -> int:
    try:
        conformers = read_xyz_file(path, parse_metadata=True, strict=True)
    except (OSError, ValueError) as exc:
        raise RerunFailedRuntimeError(f"{label} is not a readable XYZ file: {path}") from exc
    if not conformers:
        raise RerunFailedRuntimeError(f"{label} contains no readable conformers: {path}")
    return len(conformers)


def _default_output_dir(step_dir: str) -> str:
    return f"{step_dir}_rerun"


def _resolve_output_dir(
    step_dir: str,
    output_dir: str | None,
    *,
    sandbox_root: str | None,
) -> str:
    raw_output = output_dir or _default_output_dir(step_dir)
    resolved = validate_managed_path(raw_output, label="rerun output", sandbox_root=sandbox_root)
    if os.path.exists(resolved):
        raise RerunFailedUsageError(f"Rerun output directory already exists: {resolved}")
    return resolved


def run_rerun_failed(
    *,
    step_dir: str,
    config_file: str,
    step_ref: str,
    output_dir: str | None = None,
) -> RerunFailedResult:
    """Rerun the ``failed.xyz`` conformers from one calc/task workflow step."""
    if not config_file:
        raise RerunFailedUsageError("--config is required with --rerun-failed")
    if not step_ref:
        raise RerunFailedUsageError("--step is required with --rerun-failed")

    cfg = load_workflow_config(config_file)
    global_config = cfg["global"]
    steps = cfg["steps"]
    sandbox_root = resolve_sandbox_root(global_config)

    resolved_step_dir = validate_managed_path(
        step_dir,
        label="step_dir",
        sandbox_root=sandbox_root,
    )
    if not os.path.isdir(resolved_step_dir):
        raise RerunFailedUsageError(f"Step directory does not exist: {resolved_step_dir}")

    failed_path = os.path.join(resolved_step_dir, "failed.xyz")
    if not os.path.exists(failed_path):
        raise RerunFailedRuntimeError(f"failed.xyz was not found in step directory: {failed_path}")
    input_count = _read_conformer_count(failed_path, label="failed.xyz")

    step_index, step = _select_step(steps, step_ref)
    step_type = str(step.get("type", "")).lower()
    if step_type not in {"calc", "task"}:
        name = step.get("name", f"step_{step_index + 1}")
        raise RerunFailedUsageError(
            f"Step {step_index + 1} ('{name}') is type '{step_type}', not calc/task"
        )

    rerun_dir = _resolve_output_dir(resolved_step_dir, output_dir, sandbox_root=sandbox_root)
    params = step.get("params", {}) or {}
    if not isinstance(params, dict):
        raise ConfigurationError(f"Step {step_index + 1} params must be a dict")

    root_dir = os.path.dirname(resolved_step_dir)
    legacy_task_config = build_task_config(params, global_config, root_dir, steps)
    ConfigSchema.validate_calc_config(legacy_task_config)
    structured_task_config = build_structured_task_config(
        params,
        global_config,
        root_dir=root_dir,
        all_steps=steps,
    )

    result = calc.run_calc_workflow_step(
        step_dir=rerun_dir,
        input_source=failed_path,
        legacy_task_config=legacy_task_config,
        execution_config=structured_task_config,
    )

    output_count = 0
    if os.path.exists(result.output_path):
        output_count = len(read_xyz_file(result.output_path, parse_metadata=True, strict=False))

    failed_count = 0
    rerun_failed_path = os.path.join(rerun_dir, "failed.xyz")
    if os.path.exists(rerun_failed_path):
        failed_count = len(read_xyz_file(rerun_failed_path, parse_metadata=True, strict=False))

    step_name = str(step.get("name", f"step_{step_index + 1}"))
    return RerunFailedResult(
        failed_path=failed_path,
        config_file=os.path.abspath(config_file),
        step_label=f"{step_index + 1}:{step_name}",
        output_dir=rerun_dir,
        input_count=input_count,
        output_count=output_count,
        failed_count=failed_count,
    )
