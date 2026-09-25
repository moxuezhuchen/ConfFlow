#!/usr/bin/env python3

"""Rerun failed conformers from an existing calc workflow step."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..calc.runner import CalcStepRequest, CalcStepRunner
from ..config.canonical import (
    WORKFLOW_SCHEMA_VERSION_V3,
    detect_schema_version,
    load_raw_mapping,
    parse_v3_document,
    require_executable_workflow_file,
    resolve_calc_step,
    resolve_step_semantic_params,
    validate_workflow_definition,
)
from ..config.canonical.schema import SchemaProfile
from ..config.canonical.validation import ValidationProfile
from ..config.models import load_workflow_model
from ..core.exceptions import ConfigurationError
from ..core.io import read_xyz_file
from ..core.path_policy import resolve_sandbox_root, validate_managed_path
from .composition import configure_default_refine

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


def _select_step_v3(steps: list[dict[str, Any]], step_ref: str) -> tuple[int, dict[str, Any]]:
    """V3 selector — the stable step ID is the only durable selector.

    Labels and 1-based indexes are presentation/legacy concepts and never
    select a V3 step (duplicate labels would be ambiguous by construction).
    """
    ref = step_ref.strip()
    if not ref:
        raise RerunFailedUsageError("--step must not be empty")
    for index, step in enumerate(steps):
        if str(step.get("id", "")) == ref:
            return index, step
    raise RerunFailedUsageError(
        f"No workflow step with stable id '{ref}' was found; V3 rerun-failed "
        "selects by stable step id only"
    )


def _run_rerun_failed_v3(
    *,
    config_file: str,
    step_dir: str,
    step_ref: str,
    output_dir: str | None,
) -> RerunFailedResult:
    """Rerun failed conformers for one Workflow-V3 calc step (stable-ID identity)."""
    raw = load_raw_mapping(config_file)
    errors = [diagnostic for diagnostic in validate_workflow_definition(raw) if diagnostic.is_error]
    if errors:
        raise RerunFailedUsageError("; ".join(f"{d.code}: {d}" for d in errors))
    definition = parse_v3_document(raw, profile=SchemaProfile.DOCUMENT)
    steps = [
        {
            "id": step.id,
            "name": step.label or step.id,
            "type": step.type,
            "enabled": step.enabled,
            "params": resolve_step_semantic_params(
                step, definition, profile=ValidationProfile.RUNNABLE
            ),
        }
        for step in definition.steps
        if step.id is not None
    ]
    global_options = definition.global_options
    sandbox_root = resolve_sandbox_root(global_options.__dict__)

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

    step_index, step = _select_step_v3(steps, step_ref)
    step_type = str(step.get("type", "")).lower()
    if step_type not in {"calc", "task"}:
        raise RerunFailedUsageError(
            f"Step '{step['id']}' is type '{step_type}', not calc/task; "
            "V3 rerun-failed applies to calc steps only"
        )
    if Path(resolved_step_dir).name != str(step.get("id")):
        raise RerunFailedUsageError(
            f"Step directory {resolved_step_dir} does not match the selected V3 step "
            f"steps/{step['id']}; V3 step directories are keyed by stable id"
        )

    rerun_dir = _resolve_output_dir(resolved_step_dir, output_dir, sandbox_root=sandbox_root)
    params = step.get("params", {}) or {}
    if not isinstance(params, dict):
        raise ConfigurationError(f"Step '{step['id']}' params must be a dict")

    calc_config = resolve_calc_step(params, global_options)
    configure_default_refine()
    result = CalcStepRunner().run(
        CalcStepRequest(
            step_name=str(step.get("id")),
            step_dir=rerun_dir,
            input_xyz=failed_path,
            config=calc_config,
        )
    )

    output_count = 0
    if os.path.exists(result.output_path):
        output_count = len(read_xyz_file(result.output_path, parse_metadata=True, strict=False))

    failed_count = 0
    rerun_failed_path = os.path.join(rerun_dir, "failed.xyz")
    if os.path.exists(rerun_failed_path):
        failed_count = len(read_xyz_file(rerun_failed_path, parse_metadata=True, strict=False))

    return RerunFailedResult(
        failed_path=failed_path,
        config_file=os.path.abspath(config_file),
        step_label=str(step.get("id")),
        output_dir=rerun_dir,
        input_count=input_count,
        output_count=output_count,
        failed_count=failed_count,
    )


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

    # Execution-capability preflight (R3.5): a V3 document is refused before
    # any state/artifact access while V3 execution is capability-blocked, and
    # a V3 document is never resolved against V1 name/dirname state. Side
    # effect free.
    require_executable_workflow_file(config_file)

    raw = load_raw_mapping(config_file)
    if detect_schema_version(raw) == WORKFLOW_SCHEMA_VERSION_V3:
        return _run_rerun_failed_v3(
            config_file=config_file,
            step_dir=step_dir,
            step_ref=step_ref,
            output_dir=output_dir,
        )

    workflow = load_workflow_model(config_file)
    global_config = workflow.global_options
    steps = [
        {"name": step.name, "type": step.type, "enabled": step.enabled, "params": dict(step.params)}
        for step in workflow.steps
    ]
    sandbox_root = resolve_sandbox_root(global_config.__dict__)

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

    calc_config = resolve_calc_step(params, global_config)
    configure_default_refine()
    result = CalcStepRunner().run(
        CalcStepRequest(
            step_name=str(step.get("name", f"step_{step_index + 1}")),
            step_dir=rerun_dir,
            input_xyz=failed_path,
            config=calc_config,
        )
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
