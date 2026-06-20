#!/usr/bin/env python3

"""Show resolved workflow configuration without executing the workflow."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

from ..config.models import CalcStepParams, load_workflow_model

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
            resolved = CalcStepParams.from_params(params, global_config).to_runtime_dict()
        except ValueError as exc:
            resolved = _config_as_dict(global_config)
            resolved.update(params)
            resolved["config_error"] = str(exc)
    else:
        resolved = _config_as_dict(global_config)
        resolved.update(params)
        if step_type in {"confgen", "gen"} and "bond_threshold" not in resolved:
            resolved["bond_threshold"] = params.get("bond_multiplier", 1.15)

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
