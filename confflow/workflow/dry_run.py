#!/usr/bin/env python3

"""Dry-run planning for the ``confflow`` CLI.

Version-aware and strictly side-effect free: a dry run reads configuration and
inputs, plans, and prints. V2 keeps its historical output (dirnames, output
path previews, executable probes). V3 prints the stable-ID planning graph from
:class:`~confflow.workflow.plan.WorkflowV3Plan` and creates nothing — no
runtime directories, no state, no processes — because V3 execution is
capability-blocked until R4.
"""

from __future__ import annotations

import os
from math import prod
from typing import Any

from ..blocks.confgen.rotations import _parse_chain, _resolve_angle_lists
from ..config.canonical import (
    WORKFLOW_SCHEMA_VERSION_V3,
    detect_schema_version,
    load_raw_mapping,
    resolve_calc_step,
)
from ..config.models import load_workflow_model
from ..core.io import parse_gaussian_input_text
from ..core.path_policy import (
    resolve_sandbox_root,
    validate_executable_setting,
    validate_managed_path,
)
from ..core.utils import validate_xyz_file
from .helpers import as_list
from .plan import WorkflowV3Plan, build_workflow_plan
from .step_naming import build_step_dir_name_map

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
    print(
        "  calc: "
        f"iprog={config.get('iprog')} "
        f"itask={config.get('itask')} "
        f"keyword={config.get('keyword')} "
        f"cores_per_task={config.get('cores_per_task')} "
        f"max_parallel_jobs={config.get('max_parallel_jobs')} "
        f"total_memory={config.get('total_memory')}"
    )
    print(f"  gaussian_path: {_check_executable_setting(config, 'gaussian_path')}")
    print(f"  orca_path: {_check_executable_setting(config, 'orca_path')}")


def _print_v3_step_preview(step: Any, plan_params: dict[str, Any]) -> None:
    step_type = str(step.type).lower()
    if step_type == "confgen":
        print(f"  confgen combinations: {estimate_confgen_combinations(plan_params)}")
    elif step_type == "calc":
        print(
            "  calc: "
            f"iprog={plan_params.get('iprog')} "
            f"itask={plan_params.get('itask')} "
            f"keyword={plan_params.get('keyword')} "
            f"cores_per_task={plan_params.get('cores_per_task')} "
            f"total_memory={plan_params.get('total_memory')}"
        )


def _run_v3_dry_run(
    input_files: list[str],
    config_file: str,
    work_dir: str,
    checked_inputs: list[str],
) -> None:
    """Print the V3 planning graph without any runtime side effect."""
    plan = build_workflow_plan(input_files, config_file)
    assert isinstance(plan, WorkflowV3Plan)  # V3 dispatch guarantees this shape

    print("ConfFlow dry-run (Workflow V3 — planning only)")
    print(f"Config: {config_file}")
    print(f"Work dir: {work_dir} (display only; V3 planning creates no directories)")
    for path, desc in zip(input_files, checked_inputs, strict=True):
        print(f"Input: {path} ({desc})")
    print(f"Schema: {plan.source_version}")
    print(f"Definition fingerprint: {plan.definition_fingerprint}")
    print(f"Steps: {len(plan.steps)}")
    print(f"Roots: {', '.join(plan.roots) if plan.roots else '(none)'}")
    print(f"Terminals: {', '.join(plan.terminals) if plan.terminals else '(none)'}")
    print(f"Execution order: {' -> '.join(plan.topological_order)}")

    for step in plan.steps:
        print("")
        enabled_text = "enabled" if step.enabled else "disabled"
        print(f"[{step.id}] ({step.type}) {enabled_text}")
        if step.label is not None:
            print(f"  label: {step.label}")
        if step.inputs:
            print(f"  inputs: [{', '.join(step.inputs)}]")
        else:
            print("  inputs: [] (external input)")
        if step.checkpoint_from is not None:
            print(f"  checkpoint: from_step={step.checkpoint_from}")
        _print_v3_step_preview(step, step.params)


def run_dry_run(input_files: list[str], config_file: str, work_dir: str) -> None:
    """Print a workflow dry-run plan without executing workflow steps."""
    checked_inputs = [_check_input_file(path) for path in input_files]
    raw = load_raw_mapping(config_file)
    if detect_schema_version(raw) == WORKFLOW_SCHEMA_VERSION_V3:
        _run_v3_dry_run(input_files, config_file, work_dir, checked_inputs)
        return

    workflow = load_workflow_model(config_file)
    global_config = workflow.global_options
    global_dict = global_config.__dict__
    steps = [
        {"name": step.name, "type": step.type, "enabled": step.enabled, "params": dict(step.params)}
        for step in workflow.steps
    ]
    _validate_path_settings(global_dict, work_dir)

    step_dirnames, _ = build_step_dir_name_map(steps)
    current_input: str | list[str] = input_files[0] if len(input_files) == 1 else list(input_files)

    print("ConfFlow dry-run")
    print(f"Config: {config_file}")
    print(f"Work dir: {work_dir}")
    for path, desc in zip(input_files, checked_inputs):
        print(f"Input: {path} ({desc})")
    print(f"Steps: {len(steps)}")

    for idx, step in enumerate(steps, start=1):
        if not step.get("enabled", True):
            print("")
            print(f"[{idx}] {step.get('name', f'step_{idx}')} ({step.get('type', '')})")
            print("  disabled: true")
            continue
        params = step.get("params") or {}
        step_type = str(step.get("type", "")).lower()
        step_name = str(step.get("name", f"step_{idx}"))
        step_dir = os.path.join(work_dir, step_dirnames[idx - 1])
        output_path = _preview_output_path(step_dir, step_type)

        print("")
        print(f"[{idx}] {step_name} ({step_type})")
        print(f"  input: {_format_input_source(current_input)}")
        print(f"  step_dir: {step_dir}")
        print(f"  output: {output_path}")

        if step_type in {"confgen", "gen"}:
            print(f"  confgen combinations: {estimate_confgen_combinations(params)}")
        elif step_type in {"calc", "task"}:
            typed = resolve_calc_step(params, global_config)
            resolved = typed.to_runtime_dict()
            _validate_path_settings({**global_dict, **resolved}, work_dir)
            _print_calc_preview(resolved)

        current_input = output_path
