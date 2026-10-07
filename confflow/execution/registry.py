#!/usr/bin/env python3

"""V4 capability registry.

The registry is the single source of truth for the closed V4-1 vocabulary:
executor capabilities, execution adapters, result profiles, scientific
checks, and recovery profiles.  Schemas, validation, and digests all read the
vocabulary from here instead of maintaining parallel enum copies.

The default registry is built from factory functions so no mutable module-level
runtime state exists; callers may build and extend their own registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Generic, TypeVar

from ..domain.binding import Cardinality, Pairing, PortKind
from ..domain.errors import DomainError
from .contracts import (
    CheckSpec,
    ExecutionAdapterSpec,
    ExecutorCapability,
    ExecutorContract,
    PortSpec,
    RecoverySpec,
    ResultProfileSpec,
)

_SpecT = TypeVar("_SpecT")

__all__ = [
    "ExecutionRegistry",
    "RegistryLookupError",
    "build_default_registry",
    "default_registry",
]


class RegistryLookupError(LookupError):
    """Raised when a capability name is not registered."""


@dataclass(frozen=True, slots=True)
class _RegisteredCapability(Generic[_SpecT]):
    """One authoritative registry entry: descriptor plus implementation.

    The descriptor serves compilers, planners, and contract generation;
    the implementation serves runtime dispatch.  Both are registered in a
    single call, so the two can never diverge into dual authorities.
    Entries without an implementation fail closed at implementation
    resolution; production registries register real implementations for
    every published capability.
    """

    spec: _SpecT
    implementation: Any


class ExecutionRegistry:
    """A registry of V4 execution capability descriptors.

    The registry is the single authority for capability *descriptors* and
    for verifying that each published capability is backed by a real runtime
    *implementation*.  Descriptor tables (executors, adapters, profiles,
    checks, recoveries) live here; the implementations live in their owning
    modules (``profile_standard`` / ``checks_standard`` /
    ``recovery_standard`` / ``execution_adapters`` / ``programs.registry``)
    and are consulted only through the ``resolve_*`` methods below, never by
    direct imports scattered across application, batch, or remote code.
    """

    __slots__ = (
        "_executors",
        "_adapters",
        "_profiles",
        "_checks",
        "_recoveries",
    )

    def __init__(self) -> None:
        self._executors: dict[ExecutorCapability, _RegisteredCapability[ExecutorContract]] = {}
        self._adapters: dict[str, _RegisteredCapability[ExecutionAdapterSpec]] = {}
        self._profiles: dict[str, _RegisteredCapability[ResultProfileSpec]] = {}
        self._checks: dict[str, _RegisteredCapability[CheckSpec]] = {}
        self._recoveries: dict[str, _RegisteredCapability[RecoverySpec]] = {}

    # ------------------------------------------------------------------
    # Registration: descriptor plus implementation in one atomic entry
    # ------------------------------------------------------------------

    def register_executor(self, contract: ExecutorContract, implementation: Any = None) -> None:
        """Register (or replace) the atomic executor entry for a capability.

        *implementation* is the runtime executor (a class implementing the
        ``execute(work_item, context, *, should_cancel=None)`` seam, or a
        factory producing one).  Production registries always pass the real
        implementation; implementation resolution fails closed on entries
        registered without one.
        """
        if not isinstance(contract, ExecutorContract):
            raise DomainError("contract must be an ExecutorContract")
        self._executors[contract.capability] = _RegisteredCapability(contract, implementation)

    def register_adapter(self, adapter: ExecutionAdapterSpec, implementation: Any = None) -> None:
        """Register (or replace) the atomic execution-adapter entry.

        *implementation* is the runtime input-shape resolver callable.
        """
        if not isinstance(adapter, ExecutionAdapterSpec):
            raise DomainError("adapter must be an ExecutionAdapterSpec")
        self._adapters[adapter.name] = _RegisteredCapability(adapter, implementation)

    def register_profile(self, profile: ResultProfileSpec, implementation: Any = None) -> None:
        """Register (or replace) the atomic result-profile entry.

        *implementation* is the runtime profile object.
        """
        if not isinstance(profile, ResultProfileSpec):
            raise DomainError("profile must be a ResultProfileSpec")
        self._profiles[profile.name] = _RegisteredCapability(profile, implementation)

    def register_check(self, check: CheckSpec, implementation: Any = None) -> None:
        """Register (or replace) the atomic scientific-check entry.

        *implementation* is the runtime check object.
        """
        if not isinstance(check, CheckSpec):
            raise DomainError("check must be a CheckSpec")
        self._checks[check.name] = _RegisteredCapability(check, implementation)

    def register_recovery(self, recovery: RecoverySpec, implementation: Any = None) -> None:
        """Register (or replace) the atomic recovery-profile entry.

        *implementation* is a factory taking the step's program adapter (or
        ``None``) and returning the runtime policy, so adapter-scoped
        policies are always bound through this entry, never constructed
        ad hoc by executors.
        """
        if not isinstance(recovery, RecoverySpec):
            raise DomainError("recovery must be a RecoverySpec")
        self._recoveries[recovery.name] = _RegisteredCapability(recovery, implementation)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def executor(self, capability: ExecutorCapability) -> ExecutorContract:
        """Return the contract for *capability*.

        Raises
        ------
        RegistryLookupError
            Raised when the capability is not registered.
        """
        try:
            return self._executors[capability].spec
        except KeyError as exc:
            raise RegistryLookupError(f"unknown executor capability: {capability!r}") from exc

    def find_executor(self, capability: ExecutorCapability) -> ExecutorContract | None:
        """Return the contract for *capability*, or ``None``."""
        entry = self._executors.get(capability)
        return entry.spec if entry is not None else None

    def adapter(self, name: str) -> ExecutionAdapterSpec:
        """Return the adapter named *name*.

        Raises
        ------
        RegistryLookupError
            Raised when the adapter is not registered.
        """
        try:
            return self._adapters[name].spec
        except KeyError as exc:
            raise RegistryLookupError(f"unknown execution adapter: {name!r}") from exc

    def find_adapter(self, name: str) -> ExecutionAdapterSpec | None:
        """Return the adapter named *name*, or ``None``."""
        entry = self._adapters.get(name)
        return entry.spec if entry is not None else None

    def profile(self, name: str) -> ResultProfileSpec:
        """Return the result profile named *name*.

        Raises
        ------
        RegistryLookupError
            Raised when the profile is not registered.
        """
        try:
            return self._profiles[name].spec
        except KeyError as exc:
            raise RegistryLookupError(f"unknown result profile: {name!r}") from exc

    def find_profile(self, name: str) -> ResultProfileSpec | None:
        """Return the result profile named *name*, or ``None``."""
        entry = self._profiles.get(name)
        return entry.spec if entry is not None else None

    def check(self, name: str) -> CheckSpec:
        """Return the scientific check named *name*.

        Raises
        ------
        RegistryLookupError
            Raised when the check is not registered.
        """
        try:
            return self._checks[name].spec
        except KeyError as exc:
            raise RegistryLookupError(f"unknown scientific check: {name!r}") from exc

    def find_check(self, name: str) -> CheckSpec | None:
        """Return the scientific check named *name*, or ``None``."""
        entry = self._checks.get(name)
        return entry.spec if entry is not None else None

    def recovery(self, name: str) -> RecoverySpec:
        """Return the recovery profile named *name*.

        Raises
        ------
        RegistryLookupError
            Raised when the recovery profile is not registered.
        """
        try:
            return self._recoveries[name].spec
        except KeyError as exc:
            raise RegistryLookupError(f"unknown recovery profile: {name!r}") from exc

    def find_recovery(self, name: str) -> RecoverySpec | None:
        """Return the recovery profile named *name*, or ``None``."""
        entry = self._recoveries.get(name)
        return entry.spec if entry is not None else None

    # ------------------------------------------------------------------
    # Unified resolution: one entry, descriptor access for compilers,
    # concrete implementation resolution for runtime dispatch
    # ------------------------------------------------------------------

    def executor_implementation(self, capability: ExecutorCapability | str) -> Any:
        """Return the runtime executor registered for *capability*.

        The returned object is the executor class recorded in the same
        atomic entry as the compiler's contract: ``calculation`` resolves
        to the native work-item executor, ``confgen`` to the deterministic
        conformer-generation executor, and ``structure_transform`` to the
        pure structure-set transform executor.  All three implement the
        shared ``execute(work_item, context, *, should_cancel=None)`` seam
        (see the wave-1 integration report for the exact dispatch
        signatures owned by batch/application).

        Raises
        ------
        RegistryLookupError
            Raised when the capability is unknown or its entry carries no
            runtime executor.
        """
        resolved = _coerce_capability(capability)
        try:
            entry = self._executors[resolved]
        except KeyError as exc:
            raise RegistryLookupError(
                f"unknown executor capability: {resolved.value!r}; "
                f"expected one of {sorted(item.value for item in self._executors)}"
            ) from exc
        if entry.implementation is None:
            raise RegistryLookupError(
                f"executor capability {resolved.value!r} is published but has "
                "no runtime executor registered; it must not be dispatched"
            )
        return entry.implementation

    def resolve_executor(self, capability: ExecutorCapability | str) -> ExecutorContract:
        """Resolve *capability* to its executor contract.

        Accepts an :class:`ExecutorCapability` or its string value; unknown
        capabilities fail closed with the registered vocabulary in the
        message.  This is the single authority compilers, planners, and
        dispatchers consult -- never a parallel enum copy.
        """
        resolved = _coerce_capability(capability)
        try:
            return self._executors[resolved].spec
        except KeyError as exc:
            raise RegistryLookupError(
                f"unknown executor capability: {resolved.value!r}; "
                f"expected one of {sorted(item.value for item in self._executors)}"
            ) from exc

    def resolve_adapter(self, name: str) -> ExecutionAdapterSpec:
        """Resolve execution adapter *name* to its contract.

        The contract comes from the same atomic entry that carries the
        runtime input-shape resolver; entries without an implementation
        fail closed here instead of compiling and then crashing at runtime.
        """
        try:
            entry = self._adapters[name]
        except KeyError as exc:
            raise RegistryLookupError(
                f"unknown execution adapter: {name!r}; expected one of {sorted(self._adapters)}"
            ) from exc
        if entry.implementation is None:
            raise RegistryLookupError(
                f"execution adapter {name!r} is published but has no runtime "
                "implementation; it must not be used by production workflows"
            )
        return entry.spec

    def resolve_profile(self, name: str) -> ResultProfileSpec:
        """Resolve result profile *name* to its contract.

        The contract comes from the same atomic entry that carries the
        runtime profile; entries without an implementation fail closed
        here instead of compiling and then failing at runtime lookup.
        """
        try:
            entry = self._profiles[name]
        except KeyError as exc:
            raise RegistryLookupError(
                f"unknown result profile: {name!r}; expected one of {sorted(self._profiles)}"
            ) from exc
        if entry.implementation is None:
            raise RegistryLookupError(
                f"result profile {name!r} is published but has no runtime "
                "implementation; it must not be used by production workflows"
            )
        return entry.spec

    def resolve_check(self, name: str) -> CheckSpec:
        """Resolve scientific check *name* to its contract.

        The contract comes from the same atomic entry that carries the
        runtime check.
        """
        try:
            entry = self._checks[name]
        except KeyError as exc:
            raise RegistryLookupError(
                f"unknown scientific check: {name!r}; expected one of {sorted(self._checks)}"
            ) from exc
        if entry.implementation is None:
            raise RegistryLookupError(
                f"scientific check {name!r} is published but has no runtime "
                "implementation; it must not be used by production workflows"
            )
        return entry.spec

    def resolve_recovery(self, name: str, *, adapter: Any = None) -> RecoverySpec:
        """Resolve recovery profile *name* to its contract.

        The contract comes from the same atomic entry that carries the
        recovery factory.  The optional *adapter* is documented for the
        implementation accessor (:meth:`recovery_implementation`); contract
        resolution itself needs no adapter.
        """
        try:
            entry = self._recoveries[name]
        except KeyError as exc:
            raise RegistryLookupError(
                f"unknown recovery profile: {name!r}; expected one of {sorted(self._recoveries)}"
            ) from exc
        if entry.implementation is None:
            raise RegistryLookupError(
                f"recovery profile {name!r} is published but has no runtime "
                "implementation; it must not be used by production workflows"
            )
        return entry.spec

    def resolve_program(self, name: str) -> Any:
        """Resolve program *name* (or alias) to its program adapter.

        Program names are scientific vocabulary resolved through the real
        program registry (``programs.registry``): unknown programs fail
        closed here.  There is no separate program descriptor table to drift;
        the returned adapter carries the adapter/parser versions the
        producer contract advertises.
        """
        from ..programs.registry import get_program_adapter

        try:
            return get_program_adapter(name)
        except DomainError as exc:
            raise RegistryLookupError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Runtime implementation access (single authority for dispatch)
    # ------------------------------------------------------------------

    def profile_implementation(self, name: str) -> Any:
        """Return the runtime result-profile object from the same entry."""
        self.resolve_profile(name)
        return self._profiles[name].implementation

    def check_implementation(self, name: str) -> Any:
        """Return the runtime scientific-check object from the same entry."""
        self.resolve_check(name)
        return self._checks[name].implementation

    def recovery_implementation(self, name: str, *, adapter: Any = None) -> Any:
        """Return the runtime recovery policy from the same entry.

        The entry's factory is called with the step's program adapter, so
        adapter-scoped policies (the bond-scan rescue, which renders native
        input through the program adapter) are genuinely bound to it.
        Policies with no adapter scope ignore *adapter*.  An unbound
        (``adapter=None``) scan policy declines execution, per the policy's
        own contract.
        """
        self.resolve_recovery(name, adapter=adapter)
        factory = self._recoveries[name].implementation
        return factory(adapter)

    def program_adapter(self, name: str) -> Any:
        """Return the program adapter for program *name* (or alias).

        Documented equivalent of :meth:`resolve_program`.
        """
        return self.resolve_program(name)

    def adapter_implementation(self, name: str) -> Any:
        """Return the runtime input-shape resolver from the same entry."""
        self.resolve_adapter(name)
        return self._adapters[name].implementation

    # ------------------------------------------------------------------
    # Vocabulary
    # ------------------------------------------------------------------

    @property
    def capability_names(self) -> tuple[str, ...]:
        """Return registered capability names in deterministic order."""
        return tuple(sorted(capability.value for capability in self._executors))

    @property
    def adapter_names(self) -> tuple[str, ...]:
        """Return registered adapter names in deterministic order."""
        return tuple(sorted(self._adapters))

    @property
    def profile_names(self) -> tuple[str, ...]:
        """Return registered result profile names in deterministic order."""
        return tuple(sorted(self._profiles))

    @property
    def check_names(self) -> tuple[str, ...]:
        """Return registered check names in deterministic order."""
        return tuple(sorted(self._checks))

    @property
    def recovery_names(self) -> tuple[str, ...]:
        """Return registered recovery names in deterministic order."""
        return tuple(sorted(self._recoveries))


# ----------------------------------------------------------------------
# Built-in V4-1 vocabulary
# ----------------------------------------------------------------------


def _coerce_capability(capability: ExecutorCapability | str) -> ExecutorCapability:
    """Coerce a capability value to its enum member, failing closed."""
    if isinstance(capability, ExecutorCapability):
        return capability
    try:
        return ExecutorCapability(str(capability))
    except ValueError as exc:
        raise RegistryLookupError(
            f"unknown executor capability: {capability!r}; "
            f"expected one of {sorted(item.value for item in ExecutorCapability)}"
        ) from exc


_ALL_CAPABILITIES = tuple(ExecutorCapability)


def _structure_port(
    name: str,
    cardinality: Cardinality,
    pairing: Pairing,
    description: str,
) -> PortSpec:
    return PortSpec(
        name=name,
        kind=PortKind.STRUCTURE,
        cardinality=cardinality,
        pairing=pairing,
        description=description,
    )


def _artifact_port(
    name: str,
    cardinality: Cardinality,
    pairing: Pairing,
    roles: tuple[str, ...],
    description: str,
) -> PortSpec:
    return PortSpec(
        name=name,
        kind=PortKind.ARTIFACT,
        cardinality=cardinality,
        pairing=pairing,
        roles=roles,
        description=description,
    )


def _result_port(name: str, cardinality: Cardinality, pairing: Pairing) -> PortSpec:
    return PortSpec(
        name=name,
        kind=PortKind.RESULT,
        cardinality=cardinality,
        pairing=pairing,
        description="Scientific results produced by the step.",
    )


def _confgen_input_ports() -> tuple[PortSpec, ...]:
    return (
        _structure_port(
            "structure",
            Cardinality.ONE,
            Pairing.PER_STRUCTURE,
            "Seed structure to generate conformers for.",
        ),
        PortSpec(
            name="confgen_state",
            kind=PortKind.RESULT,
            cardinality=Cardinality.OPTIONAL,
            pairing=Pairing.BY_SUBJECT,
            description=(
                "Optional upstream confgen_state result bound to the same "
                "subject structure for chained generation; the value is the "
                "input StateKey, selected strictly by result identity."
            ),
        ),
    )


def _default_checks() -> tuple[CheckSpec, ...]:
    return (
        CheckSpec(
            "normal_termination",
            "confflow.contract.check.normal_termination.v1",
            "Require the native program to report normal termination.",
        ),
        CheckSpec(
            "geometry_required",
            "confflow.contract.check.geometry_required.v1",
            "Require at least one parsed geometry.",
        ),
        CheckSpec(
            "frequencies_required",
            "confflow.contract.check.frequencies_required.v1",
            "Require a parsed vibrational frequency set.",
        ),
        CheckSpec(
            "imaginary_frequency_count",
            "confflow.contract.check.imaginary_frequency_count.v1",
            "Check the count of imaginary frequencies against an expectation.",
        ),
        CheckSpec(
            "max_rmsd_from_input",
            "confflow.contract.check.max_rmsd_from_input.v1",
            "Check the RMSD between a parsed geometry and its input structure.",
        ),
        CheckSpec(
            "bond_drift",
            "confflow.contract.check.bond_drift.v1",
            "Check drift of declared bond lengths against a threshold.",
        ),
    )


def _standard_profile(checks: tuple[str, ...]) -> ResultProfileSpec:
    return ResultProfileSpec(
        name="standard",
        contract_version="confflow.contract.result_profile.standard.v1",
        supported_checks=checks,
        provides_structures=True,
        provides_results=True,
        provides_artifacts=True,
        description="Standard parser: geometries, results, and native artifacts.",
    )


def _default_profiles(checks: tuple[str, ...]) -> tuple[ResultProfileSpec, ...]:
    # NOTE (V4 repair, worker A): only profiles backed by a real runtime
    # implementation are published.  The former ``opaque`` descriptor had no
    # implementation and is omitted: it must not be declared-but-unexecutable.
    # When an opaque runtime exists, its descriptor and implementation are
    # registered atomically in ``build_default_registry`` below.
    # R2.2: ``path_endpoints`` is retired with IRC (its only consumer).
    # ``ensemble`` is RETAINED: the workflow parser hardcodes it as the
    # result profile of every retained ConfGen step
    # (``workflow/v4/parser.py``), so deleting the registry entry would
    # break retained ConfGen compilation.  Its GOAT/NEB consumers are gone;
    # the implementation (``profile_ensemble.py``) is deleted by R2.3e,
    # which must then re-point ConfGen or keep a ConfGen-owned profile.
    return (
        _standard_profile(checks),
        ResultProfileSpec(
            name="ensemble",
            contract_version="confflow.contract.result_profile.ensemble.v1",
            supported_checks=("normal_termination", "geometry_required"),
            provides_structures=True,
            provides_results=True,
            provides_artifacts=True,
            description="Conformer ensemble: many structures, energy results.",
        ),
    )


def _default_adapters() -> tuple[ExecutionAdapterSpec, ...]:
    return (
        ExecutionAdapterSpec(
            name="standard",
            contract_version="confflow.contract.adapter.standard.v1",
            capability=ExecutorCapability.CALCULATION,
            input_ports=(
                _structure_port(
                    "structure",
                    Cardinality.ONE,
                    Pairing.PER_STRUCTURE,
                    "The single structure this calculation is run for.",
                ),
                _artifact_port(
                    "checkpoint",
                    Cardinality.OPTIONAL,
                    Pairing.BY_SUBJECT,
                    ("checkpoint",),
                    "Optional checkpoint bound to the same subject structure.",
                ),
            ),
            description="Single-structure native calculation with optional checkpoint.",
        ),
    )


# NOTE (V4 repair, worker A): the former ``native_template`` adapter
# descriptor is omitted for the same reason as ``opaque`` above: it had no
# input-shape resolver implementation and must not be
# declared-but-unexecutable.  Its descriptor and resolver are registered
# atomically in ``build_default_registry`` once the resolver exists.


def _default_executors() -> tuple[ExecutorContract, ...]:
    return (
        ExecutorContract(
            capability=ExecutorCapability.CALCULATION,
            contract_version="confflow.contract.executor.calculation.v1",
            output_ports=(
                _structure_port(
                    "structures",
                    Cardinality.MANY,
                    Pairing.PER_STRUCTURE,
                    "Structures produced by the calculation.",
                ),
                _artifact_port(
                    "artifacts",
                    Cardinality.MANY,
                    Pairing.BY_SUBJECT,
                    ("checkpoint", "native_output", "trajectory"),
                    "Native artifacts produced by the calculation.",
                ),
                _result_port("results", Cardinality.MANY, Pairing.BY_SUBJECT),
            ),
            requires_adapter=True,
            description="Quantum-chemistry calculation executed by a native program.",
        ),
        ExecutorContract(
            capability=ExecutorCapability.CONFGEN,
            contract_version="confflow.contract.executor.confgen.v3",
            input_ports=_confgen_input_ports(),
            output_ports=(
                _structure_port(
                    "structures",
                    Cardinality.MANY,
                    Pairing.PER_STRUCTURE,
                    "Generated conformer ensemble.",
                ),
                _result_port("results", Cardinality.MANY, Pairing.BY_SUBJECT),
                _artifact_port(
                    "artifacts",
                    Cardinality.MANY,
                    Pairing.SINGLE,
                    ("ensemble_report", "ensemble_targets"),
                    "Conformer ensemble report and canonical target records.",
                ),
            ),
            stochastic=False,
            description=(
                "Conformer generation; deterministic by default. A seed is "
                "required only when v3 sampling requests a capped subset or "
                "when the versioned legacy capped path is used."
            ),
        ),
        ExecutorContract(
            capability=ExecutorCapability.STRUCTURE_TRANSFORM,
            contract_version="confflow.contract.executor.structure_transform.v1",
            input_ports=(
                _structure_port(
                    "structure",
                    Cardinality.ONE_OR_MORE,
                    Pairing.SINGLE,
                    "The whole structure set to transform (refine/deduplicate/filter).",
                ),
                # N3: filter-only energy source. MANY (not required) + SINGLE
                # so the whole bound result set reaches the single whole-set
                # work item; per-structure pairing happens inside the filter
                # by subject identity (a BY_SUBJECT assembly pairing would
                # have no single subject for a multi-structure set).
                # Appended last.
                PortSpec(
                    name="results",
                    kind=PortKind.RESULT,
                    cardinality=Cardinality.MANY,
                    pairing=Pairing.SINGLE,
                    description="N3 filter energy source: bind to the calculation step's results.",
                ),
            ),
            output_ports=(
                _structure_port(
                    "structures",
                    Cardinality.MANY,
                    Pairing.PER_STRUCTURE,
                    "Transformed structure set.",
                ),
                _result_port("results", Cardinality.MANY, Pairing.BY_SUBJECT),
                _artifact_port(
                    "artifacts",
                    Cardinality.MANY,
                    Pairing.SINGLE,
                    ("report",),
                    "Transform report artifacts.",
                ),
            ),
            description=(
                "Explicit structure-set transformation; never a hidden "
                "post-processing tail of a calculation."
            ),
        ),
    )


def _default_recoveries() -> tuple[RecoverySpec, ...]:
    return (
        RecoverySpec(
            "none",
            "confflow.contract.recovery.none.v1",
            _ALL_CAPABILITIES,
            "No recovery; failures are reported as-is.",
        ),
        RecoverySpec(
            "ts_rescue_scan",
            "confflow.contract.recovery.ts_rescue_scan.v1",
            (ExecutorCapability.CALCULATION,),
            "Bond scan rescue for a failed transition-state calculation (V4-2).",
        ),
    )


def build_default_registry() -> ExecutionRegistry:
    """Build a fresh registry with atomic descriptor+implementation entries.

    Every published capability is registered together with its real runtime
    implementation in a single entry: executor classes, adapter input-shape
    resolvers, profile/check objects, and recovery factories bound to the
    step's program adapter at resolve time.  A missing implementation is a
    loud :exc:`KeyError` here -- never a published-but-unexecutable
    capability downstream.

    R2.3a: the ``analysis`` executor (``AnalysisItemAdapter``) stays
    unregistered and its implementation package ``confflow.analysis``
    is deleted.
    """
    from . import execution_adapters as adapter_resolvers
    from .checks_standard import CHECKS
    from .confgen_executor import ConfgenExecutor
    from .profile_standard import PROFILES
    from .recovery_standard import NoneRecoveryPolicy, TsRescueScanPolicy
    from .transform_executor import TransformExecutor
    from .work_item_executor import WorkItemExecutor

    registry = ExecutionRegistry()
    executor_implementations = {
        ExecutorCapability.CALCULATION: WorkItemExecutor,
        ExecutorCapability.CONFGEN: ConfgenExecutor,
        ExecutorCapability.STRUCTURE_TRANSFORM: TransformExecutor,
    }
    for contract in _default_executors():
        registry.register_executor(contract, executor_implementations[contract.capability])
    adapter_implementations = {
        "standard": adapter_resolvers.resolve_standard_structure,
    }
    for adapter in _default_adapters():
        registry.register_adapter(adapter, adapter_implementations[adapter.name])
    check_specs = _default_checks()
    checks = tuple(check.name for check in check_specs)
    for check in check_specs:
        registry.register_check(check, CHECKS[check.name])
    for profile in _default_profiles(checks):
        registry.register_profile(profile, PROFILES[profile.name])
    recovery_specs = {recovery.name: recovery for recovery in _default_recoveries()}
    registry.register_recovery(recovery_specs["none"], lambda adapter: NoneRecoveryPolicy())
    registry.register_recovery(recovery_specs["ts_rescue_scan"], TsRescueScanPolicy)
    return registry


@lru_cache(maxsize=1)
def default_registry() -> ExecutionRegistry:
    """Return the shared default registry.

    The returned registry is treated as read-only by the engine; callers that
    need to extend or override capabilities build their own registry with
    :func:`build_default_registry`.
    """
    return build_default_registry()
