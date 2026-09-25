#!/usr/bin/env python3

"""V4 compiler entry points.

Compilation is pure and deterministic: parse (shape), validate (semantics),
build the typed binding graph (topology and disabled-step semantics), then
freeze the execution plan.  Equal canonical input always yields byte-identical
plan payloads; nothing reads the clock, the filesystem, or the environment.

Work-item assembly is a separate phase because it depends on run inputs and
materialized producer outputs, not on the document alone.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...domain.diagnostics import Diagnostic, diagnostic_sort_key
from ...execution.registry import ExecutionRegistry
from .document import WorkflowDefinition
from .graph import build_binding_graph
from .parser import (
    DocumentParseResult,
    load_definition_file,
    parse_workflow_document,
    parse_workflow_text_document,
)
from .plan import ExecutionPlan, build_execution_plan
from .validation import validate_definition

__all__ = [
    "CompileResult",
    "compile_definition",
    "compile_workflow",
    "compile_workflow_file",
    "compile_workflow_text",
]


@dataclass(frozen=True, slots=True)
class CompileResult:
    """Outcome of a compile request."""

    plan: ExecutionPlan | None
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def ok(self) -> bool:
        """Return whether compilation produced a plan."""
        return self.plan is not None

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        """Return error diagnostics in deterministic order."""
        return tuple(item for item in self.diagnostics if item.is_error)

    @property
    def warnings(self) -> tuple[Diagnostic, ...]:
        """Return warning diagnostics in deterministic order."""
        return tuple(item for item in self.diagnostics if not item.is_error)


def _merge(*groups: tuple[Diagnostic, ...]) -> tuple[Diagnostic, ...]:
    merged: list[Diagnostic] = []
    for group in groups:
        merged.extend(group)
    return tuple(sorted(merged, key=diagnostic_sort_key))


def _compile_parsed(
    parsed: DocumentParseResult,
    *,
    registry: ExecutionRegistry | None,
) -> CompileResult:
    if parsed.definition is None:
        return CompileResult(None, tuple(sorted(parsed.diagnostics, key=diagnostic_sort_key)))
    return compile_definition(
        parsed.definition, registry=registry, parse_diagnostics=parsed.diagnostics
    )


def compile_definition(
    definition: WorkflowDefinition,
    *,
    registry: ExecutionRegistry | None = None,
    parse_diagnostics: tuple[Diagnostic, ...] = (),
) -> CompileResult:
    """Compile a canonical definition into an execution plan."""
    validation = validate_definition(definition, registry=registry)
    if validation.validated is None:
        return CompileResult(None, _merge(parse_diagnostics, validation.diagnostics))
    graph_result = build_binding_graph(validation.validated)
    if graph_result.graph is None:
        return CompileResult(
            None,
            _merge(parse_diagnostics, validation.diagnostics, graph_result.diagnostics),
        )
    diagnostics = _merge(parse_diagnostics, validation.diagnostics, graph_result.diagnostics)
    plan = build_execution_plan(validation.validated, graph_result.graph, diagnostics)
    return CompileResult(plan, diagnostics)


def compile_workflow(
    document: Mapping[str, Any] | DocumentParseResult,
    *,
    registry: ExecutionRegistry | None = None,
) -> CompileResult:
    """Compile a raw document mapping (or parse result) into an execution plan."""
    if isinstance(document, DocumentParseResult):
        return _compile_parsed(document, registry=registry)
    if not isinstance(document, Mapping):
        raise TypeError("compile_workflow expects a mapping or a DocumentParseResult")
    return _compile_parsed(parse_workflow_document(document), registry=registry)


def compile_workflow_text(
    text: str,
    *,
    registry: ExecutionRegistry | None = None,
) -> CompileResult:
    """Compile a YAML workflow document into an execution plan."""
    return _compile_parsed(parse_workflow_text_document(text), registry=registry)


def compile_workflow_file(
    path: str | Path,
    *,
    registry: ExecutionRegistry | None = None,
) -> CompileResult:
    """Compile a workflow YAML file into an execution plan."""
    parsed = load_definition_file(path)
    if parsed.definition is None:
        return CompileResult(None, tuple(sorted(parsed.diagnostics, key=diagnostic_sort_key)))
    return compile_definition(
        parsed.definition, registry=registry, parse_diagnostics=parsed.diagnostics
    )
