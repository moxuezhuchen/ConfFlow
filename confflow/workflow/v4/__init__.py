#!/usr/bin/env python3

"""ConfFlow Workflow V4 — greenfield core.

V4 is an independent workflow engine.  It shares only the dependency-free
:mod:`confflow.domain` semantic core and the :mod:`confflow.execution`
capability registry; it never imports the V2/V3 workflow runtime, the legacy
``confflow.config`` models, or the calc subsystem.

Pipeline::

    WorkflowDocument V4
        -> parse            (confflow.workflow.v4.parser)
        -> canonical definition
        -> semantic validation
        -> typed BindingGraph
        -> deterministic compiler
        -> ExecutionPlan
        -> synthetic WorkItems (assembly)

This milestone (V4-1) does not execute native programs; it establishes the
domain model, compiler, digest axes, and architecture gates.
"""

from __future__ import annotations

from .assembly import (
    AssemblyResult,
    MaterializedOutputs,
    RunInputs,
    StepOutputs,
    assemble_work_items,
)
from .compiler import (
    CompileResult,
    compile_definition,
    compile_workflow,
    compile_workflow_file,
    compile_workflow_text,
)
from .diagnostics import DiagnosticCode, DiagnosticReason, reason_of
from .document import (
    SCHEMA_ID,
    RunInputDeclaration,
    ScientificDefaults,
    ScientificDefinition,
    StepDefinition,
    WorkflowDefinition,
)
from .fingerprint import (
    COMPILER_VERSION,
    SEMANTICS_VERSION,
    definition_payload,
    step_semantic_digest,
    step_semantic_payload,
    workflow_definition_digest,
)
from .graph import BindingGraph, ResolvedEdge, build_binding_graph, order_key
from .parser import (
    DocumentParseResult,
    WorkflowYamlError,
    convert_document,
    load_definition_file,
    parse_workflow_document,
    parse_workflow_text,
    parse_workflow_text_document,
)
from .plan import ExecutionPlan, PlannedStep, build_execution_plan
from .schema import build_workflow_json_schema, defaults_summary
from .validation import (
    ValidatedDefinition,
    ValidatedStep,
    ValidationResult,
    validate_definition,
)

__all__ = [
    # Compiler
    "CompileResult",
    "compile_definition",
    "compile_workflow",
    "compile_workflow_file",
    "compile_workflow_text",
    # Plan
    "ExecutionPlan",
    "PlannedStep",
    "build_execution_plan",
    # Assembly
    "AssemblyResult",
    "MaterializedOutputs",
    "RunInputs",
    "StepOutputs",
    "assemble_work_items",
    # Document
    "SCHEMA_ID",
    "RunInputDeclaration",
    "ScientificDefaults",
    "ScientificDefinition",
    "StepDefinition",
    "WorkflowDefinition",
    # Parser / schema
    "DocumentParseResult",
    "WorkflowYamlError",
    "convert_document",
    "load_definition_file",
    "parse_workflow_document",
    "parse_workflow_text",
    "parse_workflow_text_document",
    "build_workflow_json_schema",
    "defaults_summary",
    # Validation / graph
    "BindingGraph",
    "ResolvedEdge",
    "ValidatedDefinition",
    "ValidatedStep",
    "ValidationResult",
    "build_binding_graph",
    "validate_definition",
    "order_key",
    # Digests
    "COMPILER_VERSION",
    "SEMANTICS_VERSION",
    "definition_payload",
    "step_semantic_digest",
    "step_semantic_payload",
    "workflow_definition_digest",
    # Diagnostics
    "DiagnosticCode",
    "DiagnosticReason",
    "reason_of",
]
