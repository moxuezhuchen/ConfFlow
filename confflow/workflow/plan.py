"""Typed workflow preparation boundary."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from ..config.canonical.v2_adapter import to_canonical_workflow
from ..config.models import GlobalOptions, WorkflowConfig, load_workflow_model
from ..core.exceptions import ConfFlowError
from ..core.utils import validate_xyz_file
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
    """Validate inputs and derive the immutable execution shape.

    The workflow semantics (step identity, resolved graph, execution order and
    terminal topology) come from the canonical workflow IR; the V2 YAML shape is
    reconstructed at this boundary so the execution stack keeps receiving the
    exact mapping it always has.
    """
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
    definition = to_canonical_workflow(workflow)
    global_config, steps = definition.to_v2_execution_shape()
    by_step_name = {
        step_definition.name: steps[index] for index, step_definition in enumerate(definition.steps)
    }
    predecessors = {
        name: list(step_predecessors) for name, step_predecessors in definition.predecessors.items()
    }
    execution_order = list(definition.execution_order)
    terminal_steps = list(definition.terminal_steps)

    # Fail at plan time instead of deep inside step execution: a calc step is
    # single-input by design. Disabled calc steps never execute, so they are
    # exempt from the single-input enforcement.
    for name, step in by_step_name.items():
        step_type = str(step.get("type", "")).strip().lower()
        if step_type not in {"calc", "task"}:
            continue
        if not step.get("enabled", True):
            continue
        step_predecessors = predecessors.get(name, [])
        if len(step_predecessors) > 1:
            raise ConfFlowError(
                f"calc step {name!r} has {len(step_predecessors)} inputs; "
                "a calc step accepts exactly one input. Add a confgen step to "
                "merge them first."
            )
        if not step_predecessors and len(input_files) > 1:
            raise ConfFlowError(
                f"calc step {name!r} has no inputs but the workflow provides "
                f"{len(input_files)} initial inputs; a calc step accepts exactly "
                "one input. Add a confgen step to merge them first."
            )

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
