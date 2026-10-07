#!/usr/bin/env python3

"""Semantic validation of a canonical V4 workflow definition.

Validation resolves the registry vocabulary (executors, adapters, result
profiles, checks, recovery) against each step, resolves resources and
scheduler policy, and produces :class:`ValidatedStep` records carrying the
resolved contracts and the step semantic digest.  Binding graph topology is
validated separately in :mod:`confflow.workflow.v4.graph`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ...domain.binding import PortKind, SourceKind
from ...domain.canonical import typed_digest
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
from ...execution.energy_filter import (
    ENERGY_PARAM_KEYS,
    parse_energy_params,
    uses_energy_params,
)
from ...execution.quota import QuotaError
from ...execution.registry import ExecutionRegistry, RegistryLookupError, default_registry
from ...execution.script_registry import (
    SCRIPT_OUTPUT_CHANNELS,
    ScriptEntry,
    load_script_registry,
    validate_script_args_template,
)
from .diagnostics import DiagnosticCode, DiagnosticReason, error
from .document import (
    RunInputDeclaration,
    ScientificDefaults,
    StepDefinition,
    WorkflowDefinition,
)
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
    "resolve_executor_contract",
    "resolve_step_input_ports",
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


def resolve_executor_contract(
    step: StepDefinition, registry: ExecutionRegistry
) -> tuple[ExecutorCapability | None, ExecutorContract | None, list[Diagnostic]]:
    """Resolve one step's executor capability and registry contract.

    This is the single authority for "which executor contract does this step
    have": the capability vocabulary and the registry lookup live exactly here,
    shared by semantic validation and the producer authoring seam.  Returns
    ``(None, None, diagnostics)`` when the step names an unsupported
    capability (or ``(capability, None, diagnostics)`` when the registry
    cannot resolve it); callers decide how to surface the diagnostics.
    """
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
        return None, None, diagnostics
    try:
        contract = registry.resolve_executor(capability)
    except RegistryLookupError as exc:
        diagnostics.append(
            error(
                DiagnosticCode.CAPABILITY_ERROR,
                DiagnosticReason.UNKNOWN_EXECUTOR_CAPABILITY,
                str(exc),
                step_id=step.id,
                field_path=f"{field_path}.executor",
                details={"allowed": list(registry.capability_names)},
            )
        )
        return capability, None, diagnostics
    return capability, contract, diagnostics


def resolve_step_input_ports(
    step: StepDefinition,
    capability: ExecutorCapability,
    contract: ExecutorContract,
    registry: ExecutionRegistry,
) -> tuple[ExecutionAdapterSpec | None, tuple[PortSpec, ...] | None, list[Diagnostic]]:
    """Resolve the effective input ports of one step.

    An adapter supplies the input contract exactly when the executor contract
    declares ``requires_adapter``; otherwise the executor contract's own input
    ports are the authority.  Returns ``(adapter, input_ports, diagnostics)``
    with ``input_ports=None`` when the adapter cannot be resolved.  Shared by
    semantic validation and the producer authoring seam so neither can drift
    into a private port selection rule.
    """
    diagnostics: list[Diagnostic] = []
    field_path = f"steps.{step.id}"
    if not contract.requires_adapter:
        return None, contract.input_ports, diagnostics
    scientific = step.scientific
    adapter_name = scientific.execution_adapter if scientific is not None else None
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
        return None, None, diagnostics
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
        return None, None, diagnostics
    try:
        adapter = registry.resolve_adapter(adapter_name)
    except RegistryLookupError as exc:
        diagnostics.append(
            error(
                DiagnosticCode.CAPABILITY_ERROR,
                DiagnosticReason.MISSING_CAPABILITY_IMPLEMENTATION,
                str(exc),
                step_id=step.id,
                field_path=f"{field_path}.calculation.execution_adapter",
                details={"adapter": adapter_name},
            )
        )
        return None, None, diagnostics
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
        return None, None, diagnostics
    return adapter, adapter.input_ports, diagnostics


def _confgen_seed_requirement(native: Any) -> str | None:
    """Return why a confgen step needs a seed, or ``None`` when deterministic.

    The legacy ``native.chains`` path keeps its explicitly versioned
    stochastic semantics (seed always required). The typed v3 scope is
    deterministic by default; a seed is required only when ``sampling``
    requests a capped subset (the sole stochastic authority).
    """
    if isinstance(native, Mapping) and native.get("schema_version") == 3:
        sampling = native.get("sampling") or {}
        if isinstance(sampling, Mapping) and sampling.get("cap") is not None:
            return "v3 sampling cap requires an explicit top-level seed"
        return None
    return "legacy confgen requires an explicit seed"


def _confgen_freeze_rejection(
    scientific: Any, run_scientific_defaults: ScientificDefaults
) -> str | None:
    """Return why a confgen step's freeze declaration is rejected, if any.

    Frozen-atom constraints are unsupported by conformer generation: a
    non-empty freeze override or run default fails closed instead of being
    silently ignored.
    """
    try:
        override_freeze = scientific.overrides.get("freeze")
    except Exception:
        override_freeze = None
    if override_freeze:
        return "confgen does not support freeze overrides; declare an empty freeze"
    default_freeze = getattr(run_scientific_defaults, "freeze", None)
    if default_freeze:
        return (
            "confgen does not support frozen atoms; the run-level freeze default "
            "must be empty for confgen steps"
        )
    return None


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


def _native_option(native: Any, key: str) -> Any:
    """Return ``native[key]``, or ``None`` when absent or unmapped."""
    try:
        get = native.get
    except AttributeError:
        return None
    try:
        return get(key)
    except Exception:
        return None


#: Native sub-mapping that selects the NEB ensemble execution mode.
_NATIVE_MODES = ("neb",)


def _active_native_modes(native: Any) -> list[str]:
    """Return the native NEB mode declared by *native*.

    Mirrors the program-adapter rule: the mode is active exactly when its
    sub-mapping is present (not ``None``).
    """
    return [mode for mode in _NATIVE_MODES if _native_option(native, mode) is not None]


#: Result profiles the NEB native mode can actually execute.
_MODE_PROFILES: dict[str, tuple[str, ...]] = {
    "neb": ("ensemble",),
}


def _native_mode_profile_mismatch(
    native: Any, profile_name: str, *, native_definition_reported: bool = False
) -> str | None:
    """Describe a native-mode/result-profile mismatch, or ``None``.

    An NEB ensemble rendered into a non-ensemble profile would silently
    drop computed structures, so the combination fails closed at compile
    time.

    Native-definition shape and mode exclusivity are the program adapter's
    requirement (``validate_native_definition``); when that path already
    reported the defect, the same fact is not restated here (one defect, one
    diagnostic).  For steps the adapter path does not evaluate (disabled
    steps never render), the shape/exclusivity checks stay as the document's
    fail-closed guard, so the accept/reject outcome never changes.
    """
    modes = _active_native_modes(native)
    if not modes:
        return None
    mode = modes[0]
    if not isinstance(_native_option(native, mode), Mapping):
        if native_definition_reported:
            return None
        return f"native {mode!r} options must be a mapping"
    allowed = _MODE_PROFILES[mode]
    if profile_name not in allowed:
        return (
            f"native mode {mode!r} requires result profile "
            f"{' or '.join(repr(item) for item in allowed)}, "
            f"got {profile_name!r}"
        )
    return None


def _structure_target_ports(
    step: StepDefinition,
    capability: Any,
    contract: ExecutorContract | None,
    registry: ExecutionRegistry,
) -> set[str]:
    """Return the structure-kind input port names bound on *step*.

    Calculation input ports are adapter-resolved (standard), so the same
    resolver the step validation uses supplies the port facts here; no
    port rule is re-implemented.
    """
    if contract is None:
        return set()
    try:
        _adapter, ports, _diagnostics = resolve_step_input_ports(
            step, capability, contract, registry
        )
    except Exception:
        return set()
    if not ports:
        return set()
    return {port.name for port in ports if getattr(port, "kind", None) is PortKind.STRUCTURE}


def _producer_port_is_structure(
    producer: StepDefinition, port_name: str | None, registry: ExecutionRegistry
) -> bool:
    """Return whether *producer*'s output *port_name* carries structures.

    Both the executor contract output port and the producer's actual
    result profile must provide structures (binding validation separately
    guards the wiring itself).  Unknown producers, unresolvable
    contracts/profiles, and unknown ports are not structure provenance:
    callers fail closed and other gates report the underlying defect.
    """
    if not port_name:
        return False
    try:
        _capability, contract, _diagnostics = resolve_executor_contract(producer, registry)
    except Exception:
        return False
    if contract is None:
        return False
    try:
        output_ports = contract.output_ports
    except Exception:
        return False
    port_match = False
    for port in output_ports:
        if port.name == port_name:
            port_match = port.kind is PortKind.STRUCTURE
            break
    if not port_match:
        return False
    scientific = producer.scientific
    profile_name = (scientific.result_profile if scientific is not None else None) or "standard"
    try:
        profile = registry.resolve_profile(profile_name)
    except Exception:
        return False
    return bool(getattr(profile, "provides_structures", False))


def _proven_input_state(
    step: StepDefinition,
    field: str,
    *,
    inputs_by_name: Mapping[str, RunInputDeclaration],
    steps_by_id: Mapping[str, StepDefinition],
    run_defaults: ScientificDefaults,
    registry: ExecutionRegistry,
    _seen: frozenset[str] = frozenset(),
) -> bool:
    """Return whether *field* (charge/multiplicity) is provably available.

    Proof order: an explicit step override, the run-level default, else
    every bound structure root — run-input declarations carrying the
    field explicitly, or producer steps proven recursively through
    calculation overrides and transform/ConfGen propagation.  Distinct
    values across roots stay per-structure knowledge (never merged into
    a guessed global).  Unknown producers, cycles, disabled producers,
    and steps with no bound structure roots are unproven: fail closed.
    """
    scientific = step.scientific
    try:
        override = scientific.overrides.get(field) if scientific is not None else None
    except Exception:
        override = None
    if override is not None:
        return True
    if getattr(run_defaults, field, None) is not None:
        return True
    if step.id in _seen:
        return False
    seen = _seen | {step.id}
    try:
        capability, contract, _diagnostics = resolve_executor_contract(step, registry)
    except Exception:
        return False
    targets = _structure_target_ports(step, capability, contract, registry)
    try:
        bindings = tuple(step.bindings)
    except Exception:
        return False
    relevant = False
    for binding in bindings:
        if binding.target_port not in targets:
            continue
        source = binding.source
        if source.kind is SourceKind.RUN_INPUT:
            declaration = inputs_by_name.get(source.port)
            if declaration is None or declaration.kind is not PortKind.STRUCTURE:
                return False
            relevant = True
            if getattr(declaration, field, None) is None:
                return False
        elif source.kind is SourceKind.STEP_OUTPUT:
            producer = steps_by_id.get(source.step_id or "")
            if producer is None or not producer.enabled:
                return False
            if not _producer_port_is_structure(producer, source.port, registry):
                continue
            relevant = True
            if not _proven_input_state(
                producer,
                field,
                inputs_by_name=inputs_by_name,
                steps_by_id=steps_by_id,
                run_defaults=run_defaults,
                registry=registry,
                _seen=seen,
            ):
                return False
        else:
            return False
    return relevant


#: Digest domain marker folding a registered script's content hash into a
#: script step's semantic digest, so edited scripts become new tasks.
SCRIPT_STEP_DIGEST_KIND = "confflow.workflow.step.script.v1"


def _validate_script_step(
    step: StepDefinition,
    scripts: Mapping[str, ScriptEntry],
) -> tuple[str | None, list[Diagnostic]]:
    """Check one script step against the server table; return its content hash."""
    diagnostics: list[Diagnostic] = []
    field_path = f"steps.{step.id}"
    scientific = step.scientific
    script_id = scientific.script_id if scientific is not None else None
    if not script_id:
        diagnostics.append(
            error(
                DiagnosticCode.CAPABILITY_ERROR,
                DiagnosticReason.MISSING_EXECUTOR_BLOCK,
                "script steps require a registered script id",
                step_id=step.id,
                field_path=field_path,
            )
        )
        return None, diagnostics
    entry = scripts.get(script_id)
    if entry is None:
        diagnostics.append(
            error(
                DiagnosticCode.CAPABILITY_ERROR,
                DiagnosticReason.UNKNOWN_SCRIPT,
                f"unknown script {script_id!r}; expected one of {sorted(scripts)}",
                step_id=step.id,
                field_path=f"{field_path}.script",
                details={"allowed": sorted(scripts)},
            )
        )
        return None, diagnostics
    args = tuple(scientific.script_args) if scientific is not None else ()
    try:
        validate_script_args_template(args)
    except DomainError as exc:
        diagnostics.append(
            error(
                DiagnosticCode.CAPABILITY_ERROR,
                DiagnosticReason.INVALID_SCRIPT_ARGS,
                str(exc),
                step_id=step.id,
                field_path=f"{field_path}.args",
            )
        )
    outputs = dict(scientific.script_outputs) if scientific is not None else {}
    for channel in ("structures", "summary"):
        declared = outputs.get(channel)
        if declared is None:
            continue
        if (
            not isinstance(declared, str)
            or not declared
            or declared.startswith(("/", "\\"))
            or "/" in declared
            or "\\" in declared
            or declared == ".."
            or "/../" in f"/{declared}/"
        ):
            diagnostics.append(
                error(
                    DiagnosticCode.CAPABILITY_ERROR,
                    DiagnosticReason.INVALID_VALUE,
                    f"script outputs.{channel} must be a flat file name, got {declared!r}",
                    step_id=step.id,
                    field_path=f"{field_path}.outputs.{channel}",
                )
            )
    patterns = outputs.get("artifacts")
    if patterns is not None:
        if (
            not isinstance(patterns, (list, tuple))
            or not patterns
            or not all(
                isinstance(item, str) and item and not item.startswith(("/", "\\"))
                for item in patterns
            )
        ):
            diagnostics.append(
                error(
                    DiagnosticCode.CAPABILITY_ERROR,
                    DiagnosticReason.INVALID_VALUE,
                    "script outputs.artifacts must be a non-empty list of relative glob patterns",
                    step_id=step.id,
                    field_path=f"{field_path}.outputs.artifacts",
                )
            )
    if any(item.is_error for item in diagnostics):
        return None, diagnostics
    return entry.sha256, diagnostics


def _validate_script_output_bindings(
    step: StepDefinition,
    *,
    all_steps: tuple[StepDefinition, ...],
    registry: ExecutionRegistry,
) -> list[Diagnostic]:
    """Forbid downstream bindings to a script step's non-structures ports."""
    diagnostics: list[Diagnostic] = []
    try:
        bindings = tuple(step.bindings)
    except Exception:
        return diagnostics
    if not bindings:
        return diagnostics
    producers = {item.id: item for item in all_steps}
    for binding in bindings:
        source = binding.source
        if source.kind is not SourceKind.STEP_OUTPUT or source.step_id is None:
            continue
        if source.port == "structures":
            continue
        producer = producers.get(source.step_id)
        if producer is None:
            continue
        try:
            capability, _, _ = resolve_executor_contract(producer, registry)
        except Exception:
            continue
        if capability is ExecutorCapability.SCRIPT:
            diagnostics.append(
                error(
                    DiagnosticCode.BINDING_ERROR,
                    DiagnosticReason.SCRIPT_PORT_NOT_BINDABLE,
                    f"script step {source.step_id!r} only exposes 'structures' downstream; "
                    f"port {source.port!r} cannot be bound (fixed channels: "
                    + ", ".join(SCRIPT_OUTPUT_CHANNELS)
                    + ")",
                    step_id=step.id,
                    field_path=f"steps.{step.id}.bindings.{binding.target_port}",
                    details={"producer_step_id": source.step_id, "port": source.port},
                )
            )
    return diagnostics


def _validate_filter_energy_binding(
    step: StepDefinition,
    *,
    all_steps: tuple[StepDefinition, ...],
    registry: ExecutionRegistry,
) -> list[Diagnostic]:
    """Enforce N3 binding rules for filter steps using energy selection."""
    diagnostics: list[Diagnostic] = []
    field_path = f"steps.{step.id}"
    scientific = step.scientific
    if scientific is None or scientific.transform != "filter" or not step.enabled:
        return diagnostics
    native = scientific.native
    if uses_energy_params(native):
        try:
            parse_energy_params(native)
        except DomainError as exc:
            diagnostics.append(
                error(
                    DiagnosticCode.CAPABILITY_ERROR,
                    DiagnosticReason.INVALID_VALUE,
                    str(exc),
                    step_id=step.id,
                    field_path=f"{field_path}.transform.native",
                )
            )
            return diagnostics
    by_target = {binding.target_port: binding for binding in step.bindings}
    results_binding = by_target.get("results")
    if results_binding is None:
        if uses_energy_params(native):
            diagnostics.append(
                error(
                    DiagnosticCode.BINDING_ERROR,
                    DiagnosticReason.FILTER_RESULTS_REQUIRED,
                    "filter steps using energy parameters must bind the 'results' "
                    "input port to a calculation step's results output port",
                    step_id=step.id,
                    field_path=f"{field_path}.bindings.results",
                    details={
                        "energy_params": sorted(set(native) & set(ENERGY_PARAM_KEYS)),
                    },
                )
            )
        return diagnostics
    source = results_binding.source
    producer: StepDefinition | None = None
    if source.kind is SourceKind.STEP_OUTPUT and source.step_id is not None:
        producer = {item.id: item for item in all_steps}.get(source.step_id)
    producer_capability: ExecutorCapability | None = None
    if producer is not None:
        producer_capability, _, _ = resolve_executor_contract(producer, registry)
    structure_source_id: str | None = None
    structure_binding = by_target.get("structure")
    if structure_binding is not None and structure_binding.source.kind is (SourceKind.STEP_OUTPUT):
        structure_source_id = structure_binding.source.step_id
    if not (
        source.kind is SourceKind.STEP_OUTPUT
        and producer_capability is ExecutorCapability.CALCULATION
        and source.port == "results"
        and structure_source_id == source.step_id
    ):
        diagnostics.append(
            error(
                DiagnosticCode.BINDING_ERROR,
                DiagnosticReason.FILTER_RESULTS_SOURCE_MISMATCH,
                "filter 'results' must bind the same calculation step's results "
                "port the 'structure' input comes from",
                step_id=step.id,
                field_path=f"{field_path}.bindings.results",
                details={
                    "results_source_step": source.step_id,
                    "results_source_port": source.port,
                    "structure_source_step": structure_source_id,
                },
            )
        )
    return diagnostics


def _validate_step(
    step: StepDefinition,
    run_resources: ResourceRequest,
    run_scheduler: SchedulerPolicy,
    registry: ExecutionRegistry,
    run_scientific_defaults: ScientificDefaults,
    *,
    input_declarations: Mapping[str, RunInputDeclaration] | None = None,
    all_steps: tuple[StepDefinition, ...] | None = None,
    scripts: Mapping[str, ScriptEntry] | None = None,
) -> tuple[ValidatedStep | None, list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    field_path = f"steps.{step.id}"
    capability, contract, resolution_diagnostics = resolve_executor_contract(step, registry)
    diagnostics.extend(resolution_diagnostics)
    if capability is None or contract is None:
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
    script_sha: str | None = None
    if capability is ExecutorCapability.SCRIPT:
        script_sha, script_diagnostics = _validate_script_step(step, scripts or {})
        diagnostics.extend(script_diagnostics)
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
    program_adapter: Any = None
    if capability is ExecutorCapability.CALCULATION and scientific.program:
        # Program names are scientific vocabulary resolved through the real
        # program registry: unknown programs fail closed at compile time.
        # Role/task names never participate in dispatch.
        try:
            program_adapter = registry.resolve_program(scientific.program)
        except RegistryLookupError as exc:
            diagnostics.append(
                error(
                    DiagnosticCode.CAPABILITY_ERROR,
                    DiagnosticReason.UNKNOWN_PROGRAM,
                    str(exc),
                    step_id=step.id,
                    field_path=f"{field_path}.calculation.program",
                    details={"program": scientific.program},
                )
            )

    native_definition_reported = False
    if program_adapter is not None and step.enabled:
        # The program adapter is the file-format authority.  Its
        # structure-independent native-definition requirements (strict native
        # vocabulary, required non-empty values, deterministic option
        # constraints) are evaluated here, at compile time, on the same path
        # the renderer uses.  Anything the adapter would deterministically
        # refuse at rendering must be refused before submission; only
        # structure-dependent checks stay in the runtime.  Disabled steps
        # never render and therefore carry no native requirement.
        for native_message in program_adapter.validate_native_definition(scientific.native):
            native_definition_reported = True
            diagnostics.append(
                error(
                    DiagnosticCode.SCIENTIFIC_PARAMETER_CONFLICT,
                    DiagnosticReason.INVALID_VALUE,
                    native_message.removeprefix("native_input_error: "),
                    step_id=step.id,
                    field_path=f"{field_path}.calculation.native",
                    details={
                        "program": scientific.program,
                        "requirement": "native_definition",
                    },
                )
            )

    if capability is ExecutorCapability.CALCULATION and step.enabled:
        # Native rendering requires resolved charge and multiplicity for the
        # driving structure at execution time.  Proof order per field: an
        # explicit step override, the run-level default, else every bound
        # structure root — run-input declarations carrying the field, or
        # producer steps proven recursively through calculation overrides
        # and transform/ConfGen propagation (heterogeneous per-structure
        # values stay per-structure knowledge; the runtime resolves each
        # work item's inherited record state).  Unproven, unknown, or
        # cyclic provenance fails closed here (the same requirement the
        # work-item executor enforces before native rendering).  Disabled
        # steps never render and therefore never carry this requirement.
        inputs_by_name = dict(input_declarations) if input_declarations is not None else {}
        steps_by_id = {item.id: item for item in (all_steps or ())}
        unresolved = []
        for name in ("charge", "multiplicity"):
            if scientific.overrides.get(name) is not None:
                continue
            if getattr(run_scientific_defaults, name, None) is not None:
                continue
            if _proven_input_state(
                step,
                name,
                inputs_by_name=inputs_by_name,
                steps_by_id=steps_by_id,
                run_defaults=run_scientific_defaults,
                registry=registry,
            ):
                continue
            unresolved.append(name)
        if unresolved:
            diagnostics.append(
                error(
                    DiagnosticCode.SCIENTIFIC_PARAMETER_CONFLICT,
                    DiagnosticReason.METADATA_UNAVAILABLE,
                    "charge and multiplicity must be resolved before native rendering",
                    step_id=step.id,
                    field_path=f"{field_path}.calculation.overrides",
                    details={
                        "missing": list(unresolved),
                        "declared_sources": [
                            "calculation.overrides",
                            "global.scientific_defaults",
                            "run input declarations",
                            "upstream step overrides",
                        ],
                    },
                )
            )

    adapter: ExecutionAdapterSpec | None
    input_ports: tuple[PortSpec, ...]
    adapter, resolved_ports, port_diagnostics = resolve_step_input_ports(
        step, capability, contract, registry
    )
    diagnostics.extend(port_diagnostics)
    if resolved_ports is None:
        return None, diagnostics
    input_ports = resolved_ports

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
    try:
        profile = registry.resolve_profile(profile_name)
    except RegistryLookupError as exc:
        diagnostics.append(
            error(
                DiagnosticCode.CAPABILITY_ERROR,
                DiagnosticReason.MISSING_CAPABILITY_IMPLEMENTATION,
                str(exc),
                step_id=step.id,
                field_path=f"{field_path}.calculation.result_profile",
                details={"profile": profile_name},
            )
        )
        return None, diagnostics
    for check_name in scientific.checks:
        if registry.find_check(check_name) is None:
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
            continue
        try:
            registry.resolve_check(check_name)
        except RegistryLookupError as exc:
            diagnostics.append(
                error(
                    DiagnosticCode.CAPABILITY_ERROR,
                    DiagnosticReason.MISSING_CAPABILITY_IMPLEMENTATION,
                    str(exc),
                    step_id=step.id,
                    field_path=f"{field_path}.checks",
                    details={"check": check_name},
                )
            )
            continue
        if check_name not in profile.supported_checks:
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
    else:
        try:
            recovery = registry.resolve_recovery(scientific.recovery)
        except RegistryLookupError as exc:
            diagnostics.append(
                error(
                    DiagnosticCode.CAPABILITY_ERROR,
                    DiagnosticReason.MISSING_CAPABILITY_IMPLEMENTATION,
                    str(exc),
                    step_id=step.id,
                    field_path=f"{field_path}.calculation.recovery.profile",
                    details={"recovery": scientific.recovery},
                )
            )
            recovery = None
        if recovery is not None and capability not in recovery.supported_capabilities:
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
    if capability is ExecutorCapability.CONFGEN and step.enabled:
        seed_reason = _confgen_seed_requirement(scientific.native)
        if seed_reason is not None and scientific.seed is None:
            diagnostics.append(
                error(
                    DiagnosticCode.CAPABILITY_ERROR,
                    DiagnosticReason.SEED_REQUIRED,
                    seed_reason,
                    step_id=step.id,
                    field_path=f"{field_path}.seed",
                    details={"executor": capability.value},
                )
            )
        freeze_reason = _confgen_freeze_rejection(scientific, run_scientific_defaults)
        if freeze_reason is not None:
            diagnostics.append(
                error(
                    DiagnosticCode.SCIENTIFIC_PARAMETER_CONFLICT,
                    DiagnosticReason.INVALID_VALUE,
                    freeze_reason,
                    step_id=step.id,
                    field_path=f"{field_path}.confgen.overrides",
                    details={"executor": capability.value},
                )
            )
    if capability is ExecutorCapability.CALCULATION:
        mismatch = _native_mode_profile_mismatch(
            scientific.native,
            profile.name,
            native_definition_reported=native_definition_reported,
        )
        if mismatch is not None:
            diagnostics.append(
                error(
                    DiagnosticCode.CAPABILITY_ERROR,
                    DiagnosticReason.INCOMPATIBLE_CAPABILITY_COMBINATION,
                    mismatch,
                    step_id=step.id,
                    field_path=f"{field_path}.calculation.result_profile",
                    details={
                        "profile": profile.name,
                        "modes": _active_native_modes(scientific.native),
                    },
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
    diagnostics.extend(
        _validate_filter_energy_binding(step, all_steps=all_steps or (), registry=registry)
    )
    diagnostics.extend(
        _validate_script_output_bindings(step, all_steps=all_steps or (), registry=registry)
    )
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
    base_digest = step_semantic_digest(
        step,
        contract,
        adapter,
        profile,
        resources,
        check_versions=check_versions,
        recovery_version=recovery_version,
    )
    if capability is ExecutorCapability.SCRIPT and script_sha is not None:
        base_digest = typed_digest(
            SCRIPT_STEP_DIGEST_KIND,
            {"base": base_digest, "script_sha256": script_sha},
        )
    validated = ValidatedStep(
        definition=step,
        executor=contract,
        adapter=adapter,
        profile=profile,
        input_ports=input_ports,
        output_ports=contract.output_ports,
        resources=resources,
        scheduler=scheduler,
        step_semantic_digest=base_digest,
        check_versions=check_versions,
        recovery_version=recovery_version,
    )
    return validated, diagnostics


def validate_definition(
    definition: WorkflowDefinition,
    *,
    registry: ExecutionRegistry | None = None,
    script_registry: Mapping[str, ScriptEntry] | None = None,
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
    inputs_by_name = {item.name: item for item in definition.inputs}
    if script_registry is not None:
        script_table: Mapping[str, ScriptEntry] = script_registry
    elif any(step.executor == ExecutorCapability.SCRIPT.value for step in definition.steps):
        try:
            script_table = load_script_registry()
        except QuotaError as exc:
            diagnostics.append(
                error(
                    DiagnosticCode.CAPABILITY_ERROR,
                    DiagnosticReason.INVALID_VALUE,
                    f"cannot load the server script registry: {exc}",
                    field_path="steps",
                )
            )
            script_table = {}
    else:
        script_table = {}
    for step in definition.steps:
        validated, step_diagnostics = _validate_step(
            step,
            run_resources,
            run_scheduler,
            active,
            definition.scientific_defaults,
            input_declarations=inputs_by_name,
            all_steps=tuple(definition.steps),
            scripts=script_table,
        )
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
