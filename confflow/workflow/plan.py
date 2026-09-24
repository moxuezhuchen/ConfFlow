"""Typed workflow preparation boundary.

``build_workflow_plan`` is the version-aware planning façade:

- a **V2** document yields the historical :class:`WorkflowPlan`, the immutable
  execution shape the whole V2 runtime consumes;
- a **V3** document yields a planning-only :class:`WorkflowV3Plan` built on the
  R3.4 authoritative validated graph. It is an *inspection* representation: it
  carries no directory names, no state/binding identity and no runtime paths,
  because V3 execution is capability-blocked (R4 defines those identities).

Both versions share only the input-file probing; the semantic truth for each
comes from its own canonical path, never from a re-implementation here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..config.canonical import (
    WORKFLOW_SCHEMA_VERSION,
    WORKFLOW_SCHEMA_VERSION_V3,
    ValidatedWorkflowGraph,
    WorkflowFingerprintError,
    build_validated_graph,
    detect_schema_version,
    load_raw_mapping,
    parse_v3_document,
    resolve_step_semantic_params,
    validate_workflow_definition,
    workflow_definition_fingerprint_v3,
)
from ..config.canonical.schema import SchemaProfile
from ..config.canonical.v2_adapter import to_canonical_workflow
from ..config.canonical.validation import ValidationProfile, calc_input_diagnostics
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


@dataclass(frozen=True)
class WorkflowV3StepPlan:
    """One step of the planning-only V3 representation.

    ``params`` are the resolved semantic params (the same canonical resolution
    validation uses). Identity is exclusively the stable ``id``; ``label`` is
    display-only and never selects.
    """

    id: str
    label: str | None
    type: str
    enabled: bool
    inputs: tuple[str, ...]
    checkpoint_from: str | None
    params: dict[str, Any]


@dataclass(frozen=True)
class WorkflowV3Plan:
    """Planning-only representation of a RUNNABLE-valid V3 definition.

    Deliberately *not* a runtime/state shape: no ``dirname``, no workflow-state
    key, no binding payload, no runtime work directory, no artifact path and no
    resume metadata exist here (R4 owns those identities). The graph is the
    R3.4 validated one, and ``definition_fingerprint`` is the frozen Definition
    Fingerprint A — never an execution fingerprint.
    """

    source_version: str
    definition: CanonicalWorkflowDefinition
    graph: ValidatedWorkflowGraph
    steps: tuple[WorkflowV3StepPlan, ...]  # topological wave order, stable-ID sorted
    roots: tuple[str, ...]
    terminals: tuple[str, ...]
    topological_order: tuple[str, ...]
    definition_fingerprint: str
    input_files: list[str] = field(default_factory=list)
    original_inputs: list[str] = field(default_factory=list)


def workflow_plan_source_version(plan: Any) -> str:
    """Return the workflow schema version a plan was built from."""
    version = plan.source_version
    return version if isinstance(version, str) else str(version)


def _require_runnable_v3(raw: Any) -> None:
    """Raise the planner's error when the V3 document is not RUNNABLE-valid."""
    errors = [diagnostic for diagnostic in validate_workflow_definition(raw) if diagnostic.is_error]
    if errors:
        raise ConfFlowError("; ".join(f"{diagnostic.code}: {diagnostic}" for diagnostic in errors))


def _build_workflow_v3_plan(
    raw: dict[str, Any],
    input_files: list[str],
    original_inputs: list[str],
) -> WorkflowV3Plan:
    """Build the planning-only V3 plan from a validated document.

    Reuses the R3.4 semantic truth end to end: the RUNNABLE validation façade,
    the authoritative validated graph and the frozen definition fingerprint.
    Nothing here re-implements toposort, ancestry or id ordering.
    """
    _require_runnable_v3(raw)
    definition = parse_v3_document(raw, profile=SchemaProfile.DOCUMENT)
    graph, graph_errors = build_validated_graph(definition.steps, require_ids=True)
    if graph is None or graph_errors:
        # Unreachable after RUNNABLE validation: the façade already rejected it.
        raise ConfFlowError(
            "; ".join(f"{diagnostic.code}: {diagnostic}" for diagnostic in graph_errors)
        )

    by_id = {step.id: step for step in definition.steps if step.id is not None}
    steps = []
    for step_id in graph.topological_order:
        step = by_id[step_id]
        steps.append(
            WorkflowV3StepPlan(
                id=step.id or step_id,
                label=step.label,
                type=step.type,
                enabled=step.enabled,
                inputs=tuple(graph.predecessors[step_id]),
                checkpoint_from=step.checkpoint_from,
                params=resolve_step_semantic_params(
                    step, definition, profile=ValidationProfile.RUNNABLE
                ),
            )
        )

    try:
        fingerprint = workflow_definition_fingerprint_v3(definition)
    except WorkflowFingerprintError as exc:  # pragma: no cover - RUNNABLE ⇒ fingerprintable
        raise ConfFlowError(f"V3 definition fingerprint failed: {exc}") from exc

    return WorkflowV3Plan(
        source_version=WORKFLOW_SCHEMA_VERSION_V3,
        definition=definition,
        graph=graph,
        steps=tuple(steps),
        roots=graph.roots,
        terminals=graph.terminals,
        topological_order=graph.topological_order,
        definition_fingerprint=fingerprint,
        input_files=input_files,
        original_inputs=original_inputs,
    )


def build_workflow_plan(
    input_xyz: list[str], config_file: str, original_input_files: list[str] | None = None
) -> WorkflowPlan | WorkflowV3Plan:
    """Validate inputs and derive the immutable planning shape.

    The workflow semantics (step identity, resolved graph, execution order and
    terminal topology) come from the canonical workflow IR; the V2 YAML shape is
    reconstructed at this boundary so the execution stack keeps receiving the
    exact mapping it always has. V3 documents plan into the inspection-only
    :class:`WorkflowV3Plan` instead — the V2 execution shape is never produced
    for them.
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
    if detect_schema_version(raw) == WORKFLOW_SCHEMA_VERSION_V3:
        return _build_workflow_v3_plan(raw, input_files, original_inputs)

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
