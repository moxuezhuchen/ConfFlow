#!/usr/bin/env python3

"""Deterministic V4 execution plan.

An :class:`ExecutionPlan` is the frozen, registry-resolved output of the
compiler.  It contains no native results, no filesystem artifact paths used as
graph identity, and no V2/V3 model references.  Work items are assembled
separately from run inputs (see :mod:`confflow.workflow.v4.assembly`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...domain.completion import CompletionPolicy
from ...domain.diagnostics import Diagnostic, diagnostic_sort_key
from ...domain.resources import ResourceRequest, SchedulerPolicy
from ...execution.contracts import (
    ExecutionBinding,
    ExecutorCapability,
    PortSpec,
)
from .document import SCHEMA_ID, ScientificDefaults, ScientificDefinition
from .fingerprint import COMPILER_VERSION, SEMANTICS_VERSION, workflow_definition_digest
from .graph import BindingGraph
from .validation import ValidatedDefinition, ValidatedStep

__all__ = [
    "ExecutionPlan",
    "PlannedStep",
    "build_execution_plan",
]


@dataclass(frozen=True, slots=True)
class PlannedStep:
    """A compiled step with resolved contracts and policy."""

    step_id: str
    label: str | None
    executor: ExecutorCapability
    scientific: ScientificDefinition
    input_ports: tuple[PortSpec, ...]
    output_ports: tuple[PortSpec, ...]
    adapter_name: str | None
    profile_name: str
    executor_contract_version: str
    profile_contract_version: str
    resources: ResourceRequest
    scheduler: SchedulerPolicy
    completion: CompletionPolicy
    execution: ExecutionBinding | None
    step_semantic_digest: str

    @classmethod
    def from_validated(cls, validated: ValidatedStep) -> PlannedStep:
        """Build a planned step from a validated step."""
        definition = validated.definition
        return cls(
            step_id=validated.step_id,
            label=definition.label,
            executor=validated.executor.capability,
            scientific=definition.scientific if definition.scientific else ScientificDefinition(),
            input_ports=validated.input_ports,
            output_ports=validated.output_ports,
            adapter_name=validated.adapter.name if validated.adapter else None,
            profile_name=validated.profile.name,
            executor_contract_version=validated.executor.contract_version,
            profile_contract_version=validated.profile.contract_version,
            resources=validated.resources,
            scheduler=validated.scheduler,
            completion=definition.completion,
            execution=definition.execution,
            step_semantic_digest=validated.step_semantic_digest,
        )

    def to_payload(self) -> dict[str, Any]:
        """Return the canonical semantic payload of this step.

        Presentation fields (label) and machine execution bindings are
        excluded by design.
        """
        return {
            "step_id": self.step_id,
            "executor": self.executor.value,
            "executor_contract_version": self.executor_contract_version,
            "scientific": self.scientific.to_payload(),
            "adapter": self.adapter_name,
            "result_profile": self.profile_name,
            "profile_contract_version": self.profile_contract_version,
            "resources": self.resources.to_dict(),
            "completion": self.completion.to_dict(),
            "step_semantic_digest": self.step_semantic_digest,
        }


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Immutable, deterministic compiled workflow plan."""

    definition_digest: str
    steps: tuple[PlannedStep, ...]
    graph: BindingGraph
    scientific_defaults: ScientificDefaults
    resources: ResourceRequest
    scheduler: SchedulerPolicy
    diagnostics: tuple[Diagnostic, ...] = ()
    schema: str = SCHEMA_ID
    semantics_version: str = SEMANTICS_VERSION
    compiler_version: str = COMPILER_VERSION

    @property
    def execution_order(self) -> tuple[str, ...]:
        """Return the deterministic execution order of step ids."""
        return tuple(step.step_id for step in self.steps)

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        """Return error diagnostics (plans are only built without errors)."""
        return tuple(item for item in self.diagnostics if item.is_error)

    @property
    def warnings(self) -> tuple[Diagnostic, ...]:
        """Return warning diagnostics."""
        return tuple(item for item in self.diagnostics if not item.is_error)

    def step(self, step_id: str) -> PlannedStep | None:
        """Return the planned step with *step_id*, or ``None``."""
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None

    def to_payload(self) -> dict[str, Any]:
        """Return the canonical payload used for determinism assertions."""
        return {
            "schema": self.schema,
            "semantics_version": self.semantics_version,
            "compiler_version": self.compiler_version,
            "definition_digest": self.definition_digest,
            "scientific_defaults": self.scientific_defaults.to_payload(),
            "resources": self.resources.to_dict(),
            "scheduler": self.scheduler.to_dict(),
            "steps": [step.to_payload() for step in self.steps],
            "graph": self.graph.to_payload(),
        }


def build_execution_plan(
    validated: ValidatedDefinition,
    graph: BindingGraph,
    diagnostics: tuple[Diagnostic, ...] = (),
) -> ExecutionPlan:
    """Build the execution plan for a validated definition and graph."""
    steps = tuple(PlannedStep.from_validated(step) for step in graph.steps)
    definition_digest = workflow_definition_digest(validated.definition, validated.steps)
    return ExecutionPlan(
        definition_digest=definition_digest,
        steps=steps,
        graph=graph,
        scientific_defaults=validated.definition.scientific_defaults,
        resources=validated.resources,
        scheduler=validated.scheduler,
        diagnostics=tuple(sorted(diagnostics, key=diagnostic_sort_key)),
    )
