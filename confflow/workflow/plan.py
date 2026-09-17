"""Typed workflow preparation boundary."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from ..config.models import GlobalOptions, WorkflowConfig, load_workflow_model
from ..core.utils import validate_xyz_file
from .dag import build_step_graph, topo_order
from .step_naming import build_step_dir_name_map
from .validation import validate_inputs_compatible


@dataclass(frozen=True)
class WorkflowPlan:
    input_files: list[str]
    original_inputs: list[str]
    workflow: WorkflowConfig
    global_config: dict[str, Any]
    typed_global: GlobalOptions
    steps: list[dict[str, Any]]
    by_step_name: dict[str, dict[str, Any]]
    predecessors: dict[str, list[str]]
    execution_order: list[str]
    terminal_steps: list[str]
    step_dirnames: list[str]
    name_to_dirname: dict[str, str]


def build_workflow_plan(
    input_xyz: list[str], config_file: str, original_input_files: list[str] | None = None
) -> WorkflowPlan:
    """Validate inputs and derive the immutable execution shape."""
    input_files = [os.path.abspath(path) for path in input_xyz]
    original_inputs = (
        [os.path.abspath(path) for path in original_input_files]
        if original_input_files
        else input_files
    )
    for path in input_files:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Input file does not exist: {path}")
        validate_xyz_file(path, strict=True)

    workflow = load_workflow_model(config_file)
    legacy = workflow.as_legacy_shape()
    global_config = legacy["global"]
    steps = legacy["steps"]
    raw_predecessors, by_step_name, _declared_inputs = build_step_graph(steps)
    explicit_inputs = any("inputs" in step for step in steps)
    if explicit_inputs:
        predecessors = raw_predecessors
    else:
        ordered_names = list(by_step_name)
        predecessors = {
            name: ([ordered_names[index - 1]] if index else [])
            for index, name in enumerate(ordered_names)
        }
    execution_order = [name for wave in topo_order(predecessors) for name in wave]
    if explicit_inputs:
        predecessor_names = {
            predecessor
            for step_predecessors in predecessors.values()
            for predecessor in step_predecessors
        }
        terminal_steps = [name for name in predecessors if name not in predecessor_names]
    else:
        terminal_steps = [execution_order[-1]]

    step_dirnames, _ = build_step_dir_name_map(steps)
    step_index_by_name = {name: index for index, name in enumerate(by_step_name)}
    name_to_dirname = {name: step_dirnames[index] for name, index in step_index_by_name.items()}

    if len(input_files) > 1:
        confgen_params = next(
            (step.get("params", {}) for step in steps if step.get("type", "").lower() == "confgen"),
            None,
        )
        validate_inputs_compatible(
            input_files,
            confgen_params,
            force_consistency=global_config.get("force_consistency", False),
        )

    return WorkflowPlan(
        input_files=input_files,
        original_inputs=original_inputs,
        workflow=workflow,
        global_config=global_config,
        typed_global=workflow.global_options,
        steps=steps,
        by_step_name=by_step_name,
        predecessors=predecessors,
        execution_order=execution_order,
        terminal_steps=terminal_steps,
        step_dirnames=step_dirnames,
        name_to_dirname=name_to_dirname,
    )
