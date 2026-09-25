#!/usr/bin/env python3

"""Semantic validation of a canonical V4 workflow definition.

Validation resolves the registry vocabulary (executors, adapters, result
profiles, checks, recovery) against each step, resolves resources and
scheduler policy, and produces :class:`ValidatedStep` records carrying the
resolved contracts and the step semantic digest.  Binding graph topology is
validated separately in :mod:`confflow.workflow.v4.graph`.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...domain.diagnostics import Diagnostic, diagnostic_sort_key
from ...domain.errors import DomainError
from ...domain.resources import OnFailure, ResourceRequest, SchedulerPolicy
from ...execution.contracts import (
    ExecutionAdapterSpec,
    ExecutorCapability,
    ExecutorContract,
    PortSpec,
    ResultProfileSpec,
)
from ...execution.registry import ExecutionRegistry, default_registry
from .diagnostics import DiagnosticCode, DiagnosticReason, error
from .document import RunInputDeclaration, StepDefinition, WorkflowDefinition
from .fingerprint import step_semantic_digest
from .schema import (
    DEFAULT_CORES_PER_ITEM,
    DEFAULT_MAX_PARALLEL_ITEMS,
    DEFAULT_MEMORY_PER_ITEM,
    TRANSFORM_KINDS,
)

__all__ = [
    "ValidatedDefinition",
    "ValidatedStep",
    "ValidationResult",
    "run_input_port_spec",
    "validate_definition",
]


@dataclass(frozen=True, slots=True)
class ValidatedStep:
    """A step with its contracts, resolved policy, and semantic digest."""

    definition: StepDefinition
    executor: ExecutorContract
    adapter: ExecutionAdapterSpec | None
    profile: ResultProfileSpec
    input_ports: tuple[PortSpec, ...]
    output_ports: tuple[PortSpec, ...]
    resources: ResourceRequest
    scheduler: SchedulerPolicy
    step_semantic_digest: str
    check_versions: tuple[tuple[str, str], ...] = ()
    recovery_version: str | None = None

    @property
    def step_id(self) -> str:
        """Return the step id."""
        return self.definition.id

    def input_port(self, name: str) -> PortSpec | None:
        """Return the declared input port *name*, or ``None``."""
        for port in self.input_ports:
            if port.name == name:
                return port
        return None

    def output_port(self, name: str) -> PortSpec | None:
        """Return the declared output port *name*, or ``None``."""
        for port in self.output_ports:
            if port.name == name:
                return port
        return None


@dataclass(frozen=True, slots=True)
class ValidatedDefinition:
    """A definition with resolved per-step contracts and run-level policy."""

    definition: WorkflowDefinition
    steps: tuple[ValidatedStep, ...]
    resources: ResourceRequest
    scheduler: SchedulerPolicy
    diagnostics: tuple[Diagnostic, ...] = ()

    def step(self, step_id: str) -> ValidatedStep | None:
        """Return the validated step with *step_id*, or ``None``."""
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Outcome of semantic validation."""

    validated: ValidatedDefinition | None
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def ok(self) -> bool:
        """Return whether validation succeeded."""
        return self.validated is not None


def run_input_port_spec(declaration: RunInputDeclaration) -> PortSpec:
    """Build the port spec view of a run input declaration."""
    roles = (declaration.role,) if declaration.role else ()
    return PortSpec(
        name=declaration.name,
        kind=declaration.kind,
        cardinality=declaration.cardinality,
        pairing=declaration.effective_pairing,
        roles=roles,
        description=declaration.description or "",
    )


def _resolve_run_policy(
    definition: WorkflowDefinition,
) -> tuple[ResourceRequest, SchedulerPolicy]:
    fallback = ResourceRequest.from_values(
        cores_per_item=DEFAULT_CORES_PER_ITEM,
        memory_per_item=DEFAULT_MEMORY_PER_ITEM,
    )
    resources = definition.resources.with_defaults(fallback)
    scheduler = definition.scheduler.with_defaults(
        SchedulerPolicy(
            max_parallel_items=DEFAULT_MAX_PARALLEL_ITEMS,
            on_failure=OnFailure.CONTINUE,
        )
    )
    return resources, scheduler


def _validate_step(
    step: StepDefinition,
    run_resources: ResourceRequest,
    run_scheduler: SchedulerPolicy,
    registry: ExecutionRegistry,
) -> tuple[ValidatedStep | None, list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    field_path = f"steps.{step.id}"
    try:
        capability = ExecutorCapability(step.executor)
    except ValueError:
        diagnostics.append(
            error(
                DiagnosticCode.CAPABILITY_ERROR,
                DiagnosticReason.UNKNOWN_EXECUTOR_CAPABILITY,
                f"unknown executor capability {step.executor!r}",
                step_id=step.id,
                field_path=f"{field_path}.executor",
                details={"allowed": list(registry.capability_names)},
            )
        )
        return None, diagnostics
    contract = registry.find_executor(capability)
    if contract is None:
        diagnostics.append(
            error(
                DiagnosticCode.CAPABILITY_ERROR,
                DiagnosticReason.UNKNOWN_EXECUTOR_CAPABILITY,
                f"executor capability {capability.value!r} is not registered",
                step_id=step.id,
                field_path=f"{field_path}.executor",
                details={"allowed": list(registry.capability_names)},
            )
        )
        return None, diagnostics
    scientific = step.scientific
    if scientific is None:
        diagnostics.append(
            error(
                DiagnosticCode.CAPABILITY_ERROR,
                DiagnosticReason.MISSING_EXECUTOR_BLOCK,
                f"executor {capability.value!r} requires a scientific definition block",
                step_id=step.id,
                field_path=field_path,
            )
        )
        return None, diagnostics
    if capability is ExecutorCapability.CALCULATION and not scientific.program:
        diagnostics.append(
            error(
                DiagnosticCode.CAPABILITY_ERROR,
                DiagnosticReason.MISSING_REQUIRED_MEMBER,
                "calculation steps require a program",
                step_id=step.id,
                field_path=f"{field_path}.calculation.program",
            )
        )

    adapter: ExecutionAdapterSpec | None = None
    input_ports: tuple[PortSpec, ...]
    if contract.requires_adapter:
        adapter_name = scientific.execution_adapter
        if not adapter_name:
            diagnostics.append(
                error(
                    DiagnosticCode.CAPABILITY_ERROR,
                    DiagnosticReason.MISSING_REQUIRED_MEMBER,
                    "execution_adapter is required for this executor",
                    step_id=step.id,
                    field_path=f"{field_path}.calculation.execution_adapter",
                )
            )
            return None, diagnostics
        adapter = registry.find_adapter(adapter_name)
        if adapter is None:
            diagnostics.append(
                error(
                    DiagnosticCode.CAPABILITY_ERROR,
                    DiagnosticReason.UNKNOWN_EXECUTION_ADAPTER,
                    f"unknown execution adapter {adapter_name!r}",
                    step_id=step.id,
                    field_path=f"{field_path}.calculation.execution_adapter",
                    details={"allowed": list(registry.adapter_names)},
                )
            )
            return None, diagnostics
        if adapter.capability is not capability:
            diagnostics.append(
                error(
                    DiagnosticCode.CAPABILITY_ERROR,
                    DiagnosticReason.ADAPTER_CAPABILITY_MISMATCH,
                    f"adapter {adapter.name!r} does not support {capability.value!r}",
                    step_id=step.id,
                    field_path=f"{field_path}.calculation.execution_adapter",
                    details={
                        "adapter_capability": adapter.capability.value,
                        "step_capability": capability.value,
                    },
                )
            )
            return None, diagnostics
        input_ports = adapter.input_ports
    else:
        input_ports = contract.input_ports

    profile_name = scientific.result_profile or "standard"
    profile = registry.find_profile(profile_name)
    if profile is None:
        diagnostics.append(
            error(
                DiagnosticCode.CAPABILITY_ERROR,
                DiagnosticReason.UNKNOWN_RESULT_PROFILE,
                f"unknown result profile {profile_name!r}",
                step_id=step.id,
                field_path=f"{field_path}.calculation.result_profile",
                details={"allowed": list(registry.profile_names)},
            )
        )
        return None, diagnostics
    for check_name in scientific.checks:
        check = registry.find_check(check_name)
        if check is None:
            diagnostics.append(
                error(
                    DiagnosticCode.CAPABILITY_ERROR,
                    DiagnosticReason.UNKNOWN_SCIENTIFIC_CHECK,
                    f"unknown scientific check {check_name!r}",
                    step_id=step.id,
                    field_path=f"{field_path}.checks",
                    details={"allowed": list(registry.check_names)},
                )
            )
        elif check_name not in profile.supported_checks:
            diagnostics.append(
                error(
                    DiagnosticCode.CAPABILITY_ERROR,
                    DiagnosticReason.CHECK_NOT_SUPPORTED,
                    f"result profile {profile.name!r} does not support check {check_name!r}",
                    step_id=step.id,
                    field_path=f"{field_path}.checks",
                    details={
                        "profile": profile.name,
                        "supported": list(profile.supported_checks),
                    },
                )
            )
    recovery = registry.find_recovery(scientific.recovery)
    if recovery is None:
        diagnostics.append(
            error(
                DiagnosticCode.CAPABILITY_ERROR,
                DiagnosticReason.UNKNOWN_RECOVERY,
                f"unknown recovery profile {scientific.recovery!r}",
                step_id=step.id,
                field_path=f"{field_path}.calculation.recovery.profile",
                details={"allowed": list(registry.recovery_names)},
            )
        )
    elif capability not in recovery.supported_capabilities:
        diagnostics.append(
            error(
                DiagnosticCode.CAPABILITY_ERROR,
                DiagnosticReason.RECOVERY_UNSUPPORTED,
                f"recovery {recovery.name!r} does not support {capability.value!r}",
                step_id=step.id,
                field_path=f"{field_path}.calculation.recovery.profile",
                details={
                    "recovery": recovery.name,
                    "supported": [item.value for item in recovery.supported_capabilities],
                },
            )
        )
    if contract.stochastic and scientific.seed is None:
        diagnostics.append(
            error(
                DiagnosticCode.CAPABILITY_ERROR,
                DiagnosticReason.SEED_REQUIRED,
                f"stochastic executor {capability.value!r} requires an explicit seed",
                step_id=step.id,
                field_path=f"{field_path}.seed",
                details={"executor": capability.value},
            )
        )
    if scientific.transform is not None and scientific.transform not in TRANSFORM_KINDS:
        diagnostics.append(
            error(
                DiagnosticCode.CAPABILITY_ERROR,
                DiagnosticReason.UNKNOWN_TRANSFORM_KIND,
                f"unknown structure transform kind {scientific.transform!r}",
                step_id=step.id,
                field_path=f"{field_path}.transform.kind",
                details={"allowed": list(TRANSFORM_KINDS)},
            )
        )

    bound_ports = {binding.target_port for binding in step.bindings}
    for port in input_ports:
        if port.is_required and port.name not in bound_ports:
            diagnostics.append(
                error(
                    DiagnosticCode.CARDINALITY_ERROR,
                    DiagnosticReason.REQUIRED_INPUT_MISSING,
                    f"required input port {port.name!r} has no binding",
                    step_id=step.id,
                    field_path=f"{field_path}.bindings.{port.name}",
                    details={"port": port.name, "cardinality": port.cardinality.value},
                )
            )

    resources = step.resources.with_defaults(run_resources)
    scheduler = step.scheduler.with_defaults(run_scheduler)
    if not resources.is_resolved:
        diagnostics.append(
            error(
                DiagnosticCode.RESOURCE_ERROR,
                DiagnosticReason.UNRESOLVED_RESOURCES,
                "step resources are not fully resolved",
                step_id=step.id,
                field_path=f"{field_path}.resources",
            )
        )
    if not scheduler.is_resolved:
        diagnostics.append(
            error(
                DiagnosticCode.RESOURCE_ERROR,
                DiagnosticReason.SCHEDULER_WIDTH_INVALID,
                "step scheduler width is not resolved",
                step_id=step.id,
                field_path=f"{field_path}.scheduler",
            )
        )
    if any(item.is_error for item in diagnostics):
        return None, diagnostics
    check_versions = tuple(
        (name, registry.check(name).contract_version)
        for name in scientific.checks
        if registry.find_check(name) is not None
    )
    recovery_version = recovery.contract_version if recovery is not None else None
    validated = ValidatedStep(
        definition=step,
        executor=contract,
        adapter=adapter,
        profile=profile,
        input_ports=input_ports,
        output_ports=contract.output_ports,
        resources=resources,
        scheduler=scheduler,
        step_semantic_digest=step_semantic_digest(
            step,
            contract,
            adapter,
            profile,
            resources,
            check_versions=check_versions,
            recovery_version=recovery_version,
        ),
        check_versions=check_versions,
        recovery_version=recovery_version,
    )
    return validated, diagnostics


def validate_definition(
    definition: WorkflowDefinition,
    *,
    registry: ExecutionRegistry | None = None,
) -> ValidationResult:
    """Resolve contracts and validate all non-topology semantics."""
    active = registry if registry is not None else default_registry()
    diagnostics: list[Diagnostic] = []

    seen_steps: set[str] = set()
    for step in definition.steps:
        if step.id in seen_steps:
            diagnostics.append(
                error(
                    DiagnosticCode.IDENTITY_ERROR,
                    DiagnosticReason.DUPLICATE_STEP_ID,
                    f"duplicate step id {step.id!r}",
                    step_id=step.id,
                    field_path="steps",
                )
            )
        seen_steps.add(step.id)
    seen_inputs: set[str] = set()
    for declaration in definition.inputs:
        if declaration.name in seen_inputs:
            diagnostics.append(
                error(
                    DiagnosticCode.IDENTITY_ERROR,
                    DiagnosticReason.DUPLICATE_INPUT_NAME,
                    f"duplicate run input name {declaration.name!r}",
                    field_path=f"inputs.{declaration.name}",
                )
            )
        seen_inputs.add(declaration.name)
        try:
            run_input_port_spec(declaration)
        except DomainError as exc:
            diagnostics.append(
                error(
                    DiagnosticCode.SCHEMA_ERROR,
                    DiagnosticReason.INVALID_VALUE,
                    str(exc),
                    field_path=f"inputs.{declaration.name}",
                )
            )

    run_resources, run_scheduler = _resolve_run_policy(definition)
    steps: list[ValidatedStep] = []
    for step in definition.steps:
        validated, step_diagnostics = _validate_step(step, run_resources, run_scheduler, active)
        diagnostics.extend(step_diagnostics)
        if validated is not None:
            steps.append(validated)
    if any(item.is_error for item in diagnostics):
        return ValidationResult(None, tuple(sorted(diagnostics, key=diagnostic_sort_key)))
    validated_definition = ValidatedDefinition(
        definition=definition,
        steps=tuple(steps),
        resources=run_resources,
        scheduler=run_scheduler,
        diagnostics=tuple(sorted(diagnostics, key=diagnostic_sort_key)),
    )
    return ValidationResult(
        validated_definition, tuple(sorted(diagnostics, key=diagnostic_sort_key))
    )
