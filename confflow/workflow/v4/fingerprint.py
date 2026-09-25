#!/usr/bin/env python3

"""V4 digest axes.

Four independent axes are defined and never mixed:

- :func:`workflow_definition_digest` covers scientific and dataflow meaning:
  resolved scientific defaults, named run inputs, per-step science, bindings,
  completion policy, and resources.  Labels, annotations, GUI placement, and
  scheduler width are excluded.
- :func:`step_semantic_digest` covers one step's science, its contract
  versions, resources, checks, recovery, and seed.  Scheduler width and
  machine execution bindings are excluded.
- work-item digests (computed during assembly) add bound input content.
- :class:`~confflow.execution.contracts.ExecutionEnvironment` has its own
  digest for *where* a computation ran.

Canonicalization is RFC 8785 JCS with a domain-separated ``kind`` marker, via
:func:`confflow.domain.canonical.typed_digest`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from ...domain.canonical import typed_digest
from ...domain.stochastic import SeedPolicy
from ...execution.contracts import ExecutionAdapterSpec, ExecutorContract, ResultProfileSpec
from .document import StepDefinition, WorkflowDefinition

if TYPE_CHECKING:
    from ...domain.resources import ResourceRequest

__all__ = [
    "COMPILER_VERSION",
    "DEFINITION_DIGEST_KIND",
    "SEMANTICS_VERSION",
    "STEP_DIGEST_KIND",
    "definition_payload",
    "step_semantic_digest",
    "step_semantic_payload",
    "workflow_definition_digest",
]

#: Semantic version of the V4 digest contracts.
SEMANTICS_VERSION: Final[str] = "confflow.workflow.v4.semantics.v1"

#: Compiler implementation version recorded in plans.
COMPILER_VERSION: Final[str] = "confflow.workflow.v4.compiler.v1"

STEP_DIGEST_KIND: Final[str] = "confflow.workflow.step.v1"
DEFINITION_DIGEST_KIND: Final[str] = "confflow.workflow.definition.v1"


def step_semantic_payload(
    step: StepDefinition,
    contract: ExecutorContract,
    adapter: ExecutionAdapterSpec | None,
    profile: ResultProfileSpec,
    resources: ResourceRequest,
    *,
    check_versions: tuple[tuple[str, str], ...],
    recovery_version: str | None,
) -> dict[str, Any]:
    """Build the canonical payload for a step semantic digest."""
    scientific = step.scientific
    science_payload = scientific.to_payload() if scientific is not None else None
    return {
        "semantics_version": SEMANTICS_VERSION,
        "step_id": step.id,
        "enabled": step.enabled,
        "executor": {
            "capability": contract.capability.value,
            "contract_version": contract.contract_version,
        },
        "adapter": (
            {
                "name": adapter.name,
                "contract_version": adapter.contract_version,
            }
            if adapter is not None
            else None
        ),
        "result_profile": {
            "name": profile.name,
            "contract_version": profile.contract_version,
        },
        "checks": [{"name": name, "contract_version": version} for name, version in check_versions],
        "recovery": {
            "profile": scientific.recovery if scientific is not None else "none",
            "contract_version": recovery_version,
        },
        "science": science_payload,
        "seed_policy": SeedPolicy.EXPLICIT.value,
        "resources": resources.to_dict(),
    }


def step_semantic_digest(
    step: StepDefinition,
    contract: ExecutorContract,
    adapter: ExecutionAdapterSpec | None,
    profile: ResultProfileSpec,
    resources: ResourceRequest,
    *,
    check_versions: tuple[tuple[str, str], ...] = (),
    recovery_version: str | None = None,
) -> str:
    """Compute the semantic digest of one step."""
    return typed_digest(
        STEP_DIGEST_KIND,
        step_semantic_payload(
            step,
            contract,
            adapter,
            profile,
            resources,
            check_versions=check_versions,
            recovery_version=recovery_version,
        ),
    )


def definition_payload(
    definition: WorkflowDefinition,
    validated_steps: tuple[Any, ...],
) -> dict[str, Any]:
    """Build the canonical scientific/dataflow payload of a definition.

    *validated_steps* are :class:`~confflow.workflow.v4.validation.ValidatedStep`
    records; they are accepted structurally to avoid a circular import.
    """
    ordered = sorted(validated_steps, key=lambda item: item.step_id)
    step_payloads: list[dict[str, Any]] = []
    for validated in ordered:
        step = validated.definition
        step_payloads.append(
            {
                "id": step.id,
                "enabled": step.enabled,
                "step_semantic_digest": validated.step_semantic_digest,
                "bindings": [
                    binding.to_dict()
                    for binding in sorted(step.bindings, key=lambda item: item.target_port)
                ],
                "completion": step.completion.to_dict(),
            }
        )
    return {
        "schema": definition.schema,
        "semantics_version": SEMANTICS_VERSION,
        "scientific_defaults": definition.scientific_defaults.to_payload(),
        "inputs": [
            declaration.to_dict()
            for declaration in sorted(definition.inputs, key=lambda item: item.name)
        ],
        "steps": step_payloads,
    }


def workflow_definition_digest(
    definition: WorkflowDefinition,
    validated_steps: tuple[Any, ...],
) -> str:
    """Compute the definition digest (scientific and dataflow meaning)."""
    return typed_digest(DEFINITION_DIGEST_KIND, definition_payload(definition, validated_steps))
