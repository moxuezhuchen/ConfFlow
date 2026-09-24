#!/usr/bin/env python3

"""Show resolved workflow configuration without executing the workflow.

``show_resolved_config`` is version-aware: a V2 document keeps the historical
name/index identity and output shape; a V3 document is shown under its stable-ID
identity (the persisted ``id``), with the declared graph, checkpoint references
and the Definition Fingerprint. Labels are display-only in V3 and never select.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

from ..config.canonical import (
    WORKFLOW_SCHEMA_VERSION_V3,
    detect_schema_version,
    load_raw_mapping,
    resolve_calc_step,
)
from ..config.models import load_workflow_model
from ..shared.confgen_params import resolve_confgen_params
from ..shared.defaults import DEFAULT_MAX_PARALLEL_JOBS
from .plan import WorkflowV3Plan, WorkflowV3StepPlan, build_workflow_plan

__all__ = [
    "show_resolved_config",
]


def _select_step(steps: list[dict[str, Any]], step_ref: str) -> tuple[int, dict[str, Any]]:
    """Select a step by name or 1-based index.

    Parameters
    ----------
    steps : list[dict]
        The workflow steps list.
    step_ref : str
        Step name or 1-based index string.

    Returns
    -------
    tuple[int, dict]
        (0-based index, step dict)

    Raises
    ------
    ValueError
        If step_ref is invalid or not found.
    """
    ref = step_ref.strip()
    if not ref:
        raise ValueError("--step must not be empty")

    if ref.isdigit():
        index = int(ref)
        if 1 <= index <= len(steps):
            return index - 1, steps[index - 1]
        raise ValueError(f"--step index {ref} is out of range (1..{len(steps)})")

    matches = [(idx, step) for idx, step in enumerate(steps) if str(step.get("name", "")) == ref]
    if not matches:
        raise ValueError(f"No workflow step named '{step_ref}' was found")
    if len(matches) > 1:
        raise ValueError(f"Workflow step name is ambiguous: {step_ref}")
    return matches[0]


def _config_as_dict(config: Any) -> dict[str, Any]:
    if isinstance(config, dict):
        return dict(config)
    if is_dataclass(config):
        return asdict(config)
    raw = getattr(config, "__dict__", None)
    return dict(raw) if isinstance(raw, dict) else {}


def _select_v3_step(plan: WorkflowV3Plan, step_ref: str) -> WorkflowV3StepPlan:
    """Select a V3 step by its exact stable ID — the only V3 identity.

    Labels are optional, duplicateable and non-identifying, and document
    positions are not identities, so neither may select. The error text says
    what was refused rather than guessing a step.
    """
    ref = step_ref.strip()
    if not ref:
        raise ValueError("--step must not be empty")
    if ref.isdigit():
        raise ValueError(
            f"V3 steps are identified by stable id, not by document index: {step_ref!r}. "
            "Use the step id, e.g. --step s001."
        )
    matches = [step for step in plan.steps if step.id == ref]
    if not matches:
        label_matches = [step for step in plan.steps if step.label == ref]
        if label_matches:
            raise ValueError(
                f"V3 step labels are not identities: {step_ref!r} matches a label. "
                "Use the stable step id, e.g. --step s001."
            )
        raise ValueError(f"No workflow step with stable id {step_ref!r} was found")
    return matches[0]


def _v3_step_resolved(plan: WorkflowV3Plan, step: WorkflowV3StepPlan) -> dict[str, Any]:
    return {
        "step_id": step.id,
        "label": step.label,
        "step_type": step.type,
        "enabled": step.enabled,
        "inputs": list(step.inputs) if step.inputs else "external-input",
        "checkpoint_from": step.checkpoint_from,
        "resolved_config": dict(step.params),
    }


def _show_v3_resolved_config(
    config_file: str,
    *,
    step_ref: str | None,
    output_format: str,
) -> None:
    """Show a V3 workflow under its stable-ID identity (planning-only)."""
    plan = build_workflow_plan([], config_file)
    assert isinstance(plan, WorkflowV3Plan)  # V3 dispatch guarantees this shape
    if step_ref is not None:
        step = _select_v3_step(plan, step_ref)
        if output_format == "json":
            output = {
                "config_file": config_file,
                "schema_version": plan.source_version,
                "definition_fingerprint": plan.definition_fingerprint,
                "step": _v3_step_resolved(plan, step),
            }
            print(json.dumps(output, indent=2, default=str))
        else:
            print(f"Config: {config_file}")
            print(f"Schema: {plan.source_version}")
            print(f"Definition fingerprint: {plan.definition_fingerprint}")
            print(f"Step [{step.id}] ({step.type}) enabled={step.enabled}")
            print()
            print(_format_text_section("Resolved config", dict(step.params)))
        return

    if output_format == "json":
        output = {
            "config_file": config_file,
            "schema_version": plan.source_version,
            "definition_fingerprint": plan.definition_fingerprint,
            "graph_order": list(plan.topological_order),
            "steps": [_v3_step_resolved(plan, step) for step in plan.steps],
        }
        print(json.dumps(output, indent=2, default=str))
    else:
        print(f"Config: {config_file}")
        print(f"Schema: {plan.source_version}")
        print(f"Definition fingerprint: {plan.definition_fingerprint}")
        print(f"Steps: {len(plan.steps)} (identity: stable id)")
        print(f"Graph order: {' -> '.join(plan.topological_order)}")
        print()
        print(
            _format_text_section("Global config", _config_as_dict(plan.definition.global_options))
        )
        for step in plan.steps:
            print()
            print(f"[{step.id}] ({step.type}) enabled={step.enabled}")
            if step.label is not None:
                print(f"  label: {step.label}")
            inputs = list(step.inputs) if step.inputs else "external-input"
            print(f"  inputs: {inputs}")
            if step.checkpoint_from is not None:
                print(f"  checkpoint: from_step={step.checkpoint_from}")
            print(_format_text_section("Resolved config", dict(step.params), indent=2))


def _resolve_step_config(
    step: dict[str, Any],
    global_config: Any,
    *,
    root_dir: str | None = None,
    all_steps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve a single step's merged configuration."""
    params = step.get("params") or {}
    if not isinstance(params, dict):
        params = {}

    step_type = str(step.get("type", "calc")).lower()
    if step_type in {"calc", "task"}:
        del root_dir, all_steps
        try:
            resolved = resolve_calc_step(params, global_config).to_runtime_dict()
        except ValueError as exc:
            resolved = _config_as_dict(global_config)
            resolved.update(params)
            resolved["config_error"] = str(exc)
    else:
        resolved = _config_as_dict(global_config)
        resolved.update(params)
        if step_type in {"confgen", "gen"}:
            resolved.update(
                resolve_confgen_params(
                    params,
                    default_workers=resolved.get("max_parallel_jobs", DEFAULT_MAX_PARALLEL_JOBS),
                )
            )

    resolved["step_type"] = step.get("type", "calc")
    resolved["step_name"] = step.get("name", "unnamed")
    return resolved


def _format_text_section(title: str, data: dict[str, Any], indent: int = 2) -> str:
    """Format a config section as readable text."""
    lines = [f"{title}:"]
    prefix = " " * indent
    for key, value in sorted(data.items()):
        lines.append(f"{prefix}{key}: {value}")
    return "\n".join(lines)


def show_resolved_config(
    config_file: str,
    *,
    step_ref: str | None = None,
    output_format: str = "text",
) -> None:
    """Show the resolved configuration for a workflow YAML.

    Parameters
    ----------
    config_file : str
        Path to the workflow YAML configuration file.
    step_ref : str or None
        Optional step name or 1-based index to show only one step.
    output_format : str
        ``"text"`` (default) for human-readable output, ``"json"`` for JSON.

    Raises
    ------
    FileNotFoundError
        If the configuration file does not exist.
    ConfigurationError
        If the configuration is invalid.
    ValueError
        If step_ref is invalid.
    """
    raw = load_raw_mapping(config_file)
    if detect_schema_version(raw) == WORKFLOW_SCHEMA_VERSION_V3:
        _show_v3_resolved_config(config_file, step_ref=step_ref, output_format=output_format)
        return

    workflow = load_workflow_model(config_file)
    global_config = workflow.global_options
    global_output = _config_as_dict(global_config)
    steps = [
        {"name": step.name, "type": step.type, "enabled": step.enabled, "params": dict(step.params)}
        for step in workflow.steps
    ]
    root_dir = None

    if step_ref is not None:
        # Show only the specified step
        step_index, step = _select_step(steps, step_ref)
        resolved = _resolve_step_config(
            step,
            global_config,
            root_dir=root_dir,
            all_steps=steps,
        )
        step_name = str(step.get("name", f"step_{step_index + 1}"))

        if output_format == "json":
            output = {
                "config_file": config_file,
                "step_index": step_index + 1,
                "step_name": step_name,
                "step_type": str(step.get("type", "")),
                "resolved_config": resolved,
            }
            print(json.dumps(output, indent=2, default=str))
        else:
            print(f"Config: {config_file}")
            print(f"Step [{step_index + 1}]: {step_name} ({step.get('type', '')})")
            print()
            print(_format_text_section("Resolved config", resolved))
    else:
        # Show global config and all steps
        if output_format == "json":
            steps_output = []
            for idx, step in enumerate(steps):
                resolved = _resolve_step_config(
                    step,
                    global_config,
                    root_dir=root_dir,
                    all_steps=steps,
                )
                steps_output.append(
                    {
                        "step_index": idx + 1,
                        "step_name": str(step.get("name", f"step_{idx + 1}")),
                        "step_type": str(step.get("type", "")),
                        "resolved_config": resolved,
                    }
                )
            output = {
                "config_file": config_file,
                "global_config": global_output,
                "steps": steps_output,
            }
            print(json.dumps(output, indent=2, default=str))
        else:
            print(f"Config: {config_file}")
            print(f"Steps: {len(steps)}")
            print()
            print(_format_text_section("Global config", global_output))
            for idx, step in enumerate(steps):
                resolved = _resolve_step_config(
                    step,
                    global_config,
                    root_dir=root_dir,
                    all_steps=steps,
                )
                step_name = str(step.get("name", f"step_{idx + 1}"))
                step_type = str(step.get("type", ""))
                print()
                print(_format_text_section(f"[{idx + 1}] {step_name} ({step_type})", resolved))
