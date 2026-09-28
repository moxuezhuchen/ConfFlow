"""Typed workflow preparation boundary.

``build_workflow_plan`` builds the historical :class:`WorkflowPlan`, the
immutable execution shape the V2 diagnostic paths (dry-run / config-show)
consume. It accepts only the released V2 document shape; the never-released V3
planning representation (``WorkflowV3Plan``) was retired by the Architecture
Diet PR-7, so a V3 document now fails closed at version recognition.

The workflow semantics (step identity, resolved graph, execution order and
terminal topology) come from the canonical workflow IR; the V2 YAML shape is
reconstructed at this boundary so the diagnostic stack keeps receiving the
exact mapping it always has.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..config.canonical import (
    WORKFLOW_SCHEMA_VERSION,
    detect_schema_version,
    load_raw_mapping,
)
from ..config.canonical.v2_adapter import to_canonical_workflow
from ..config.canonical.validation import calc_input_diagnostics
from ..config.models import GlobalOptions, WorkflowConfig, load_workflow_model
from ..core.exceptions import ConfFlowError
from ..core.utils import validate_xyz_file
from .step_naming import build_step_dir_name_map
from .validation import validate_inputs_compatible

if TYPE_CHECKING:
    from ..config.canonical.workflow import CanonicalWorkflowDefinition


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
    source_version: str = WORKFLOW_SCHEMA_VERSION


def workflow_plan_source_version(plan: Any) -> str:
    """Return the workflow schema version a plan was built from."""
    version = plan.source_version
    return version if isinstance(version, str) else str(version)


def build_workflow_plan(
    input_xyz: list[str], config_file: str, original_input_files: list[str] | None = None
) -> WorkflowPlan:
    """Validate inputs and derive the immutable planning shape.

    Only the released V2 document shape is accepted. The schema version is
    recognised once via the canonical ``detect_schema_version`` truth, so an
    unknown or retired version (including the never-released V3 wire) fails
    closed before any planning happens.
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

    raw = load_raw_mapping(config_file)
    detect_schema_version(raw)

    workflow = load_workflow_model(config_file)
    definition: CanonicalWorkflowDefinition = to_canonical_workflow(workflow)
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
    # exempt from the single-input enforcement. The canonical validator owns the
    # rule; this boundary turns its first finding into the planner's error.
    cardinality_errors = calc_input_diagnostics(definition, input_file_count=len(input_files))
    if cardinality_errors:
        raise ConfFlowError(cardinality_errors[0].message)

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
