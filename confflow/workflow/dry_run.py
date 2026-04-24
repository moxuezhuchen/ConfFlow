#!/usr/bin/env python3

"""Dry-run planning for the ``confflow`` CLI."""

from __future__ import annotations

import os
from math import prod
from typing import Any

from ..blocks.confgen.rotations import _parse_chain, _resolve_angle_lists
from ..config.loader import load_workflow_config_file
from ..config.schema import ConfigSchema
from ..core.console import console
from ..core.io import parse_gaussian_input_text
from ..core.path_policy import (
    resolve_sandbox_root,
    validate_executable_setting,
    validate_managed_path,
)
from ..core.utils import validate_xyz_file
from .helpers import as_list
from .step_naming import build_step_dir_name_map
from .task_config import build_task_config

__all__ = [
    "estimate_confgen_combinations",
    "run_dry_run",
]


def _check_input_file(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in {".gjf", ".com"}:
        with open(path, encoding="utf-8", errors="ignore") as f:
            parsed = parse_gaussian_input_text(f.read())
        if not parsed.get("atoms"):
            raise ValueError(f"Gaussian input does not contain a geometry section: {path}")
        return f"Gaussian input: {len(parsed['atoms'])} atoms"

    valid, frames = validate_xyz_file(path, strict=True)
    if not valid or not frames:
        raise ValueError(f"no valid XYZ frames found: {path}")
    return f"XYZ: {len(frames)} frame(s)"


def estimate_confgen_combinations(params: dict[str, Any]) -> int:
    """Estimate the number of torsion angle combinations for a confgen step."""
    chains = as_list(params.get("chains", params.get("chain"))) or []
    if not chains:
        return 0

    parsed_chains = [_parse_chain(str(chain)) for chain in chains]
    angle_step = int(params.get("angle_step", 120))
    per_chain_angles = _resolve_angle_lists(
        parsed_chains,
        as_list(params.get("chain_steps", params.get("steps"))),
        as_list(params.get("chain_angles", params.get("angles"))),
        angle_step,
    )
    angle_counts = [len(angles) for chain in per_chain_angles for angles in chain]
    return int(prod(angle_counts)) if angle_counts else 0


def _format_input_source(current_input: str | list[str]) -> str:
    if isinstance(current_input, list):
        return ", ".join(current_input)
    return current_input


def _preview_output_path(step_dir: str, step_type: str) -> str:
    if step_type in {"confgen", "gen"}:
        return os.path.join(step_dir, "search.xyz")
    if step_type in {"calc", "task"}:
        return os.path.join(step_dir, "result.xyz")
    return os.path.join(step_dir, "output.xyz")


def _validate_path_settings(config: dict[str, Any], work_dir: str) -> None:
    sandbox_root = resolve_sandbox_root(config)
    if sandbox_root:
        validate_managed_path(sandbox_root, label="sandbox_root")
    validate_managed_path(work_dir, label="work_dir", sandbox_root=sandbox_root)

    backup_dir = config.get("backup_dir")
    if backup_dir:
        validate_managed_path(str(backup_dir), label="backup_dir", sandbox_root=sandbox_root)


def _check_executable_setting(config: dict[str, Any], key: str) -> str:
    raw = config.get(key)
    if raw is None or str(raw).strip() == "":
        return "not set"

    allowed = config.get("allowed_executables")
    if isinstance(allowed, str):
        allowed = [item.strip() for item in allowed.split(",") if item.strip()]
    executable = validate_executable_setting(
        raw,
        label=key,
        allowed_executables=allowed,
    )
    if os.path.isabs(executable) and not os.path.exists(executable):
        return f"missing: {executable}"
    if os.path.isabs(executable):
        return f"ok: {executable}"
    return f"{executable} (not executed; PATH availability not checked)"


def _print_calc_preview(config: dict[str, Any]) -> None:
    console.print(
        "  calc: "
        f"iprog={config.get('iprog')} "
        f"itask={config.get('itask')} "
        f"keyword={config.get('keyword')} "
        f"cores_per_task={config.get('cores_per_task')} "
        f"max_parallel_jobs={config.get('max_parallel_jobs')} "
        f"total_memory={config.get('total_memory')}"
    )
    console.print(f"  gaussian_path: {_check_executable_setting(config, 'gaussian_path')}")
    console.print(f"  orca_path: {_check_executable_setting(config, 'orca_path')}")


def run_dry_run(input_files: list[str], config_file: str, work_dir: str) -> None:
    """Print a workflow dry-run plan without executing workflow steps."""
    checked_inputs = [_check_input_file(path) for path in input_files]
    cfg = load_workflow_config_file(config_file)
    global_config = cfg["global"]
    steps = cfg["steps"]
    _validate_path_settings(global_config, work_dir)

    step_dirnames, _ = build_step_dir_name_map(steps)
    current_input: str | list[str] = input_files[0] if len(input_files) == 1 else list(input_files)

    console.print("ConfFlow dry-run")
    console.print(f"Config: {config_file}")
    console.print(f"Work dir: {work_dir}")
    for path, desc in zip(input_files, checked_inputs):
        console.print(f"Input: {path} ({desc})")
    console.print(f"Steps: {len(steps)}")

    for idx, step in enumerate(steps, start=1):
        params = step.get("params") or {}
        step_type = str(step.get("type", "")).lower()
        step_name = str(step.get("name", f"step_{idx}"))
        step_dir = os.path.join(work_dir, step_dirnames[idx - 1])
        output_path = _preview_output_path(step_dir, step_type)

        console.print("")
        console.print(f"[{idx}] {step_name} ({step_type})")
        console.print(f"  input: {_format_input_source(current_input)}")
        console.print(f"  step_dir: {step_dir}")
        console.print(f"  output: {output_path}")

        if step_type in {"confgen", "gen"}:
            console.print(f"  confgen combinations: {estimate_confgen_combinations(params)}")
        elif step_type in {"calc", "task"}:
            resolved = build_task_config(params, global_config, work_dir, steps)
            ConfigSchema.validate_calc_config(resolved)
            _validate_path_settings({**global_config, **resolved}, work_dir)
            _print_calc_preview(resolved)

        current_input = output_path
