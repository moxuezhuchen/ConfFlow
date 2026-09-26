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

from functools import lru_cache

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

__all__ = [
    "ExecutionRegistry",
    "RegistryLookupError",
    "build_default_registry",
    "default_registry",
]


class RegistryLookupError(LookupError):
    """Raised when a capability name is not registered."""


class ExecutionRegistry:
    """A registry of V4 execution capability descriptors."""

    __slots__ = ("_executors", "_adapters", "_profiles", "_checks", "_recoveries")

    def __init__(self) -> None:
        self._executors: dict[ExecutorCapability, ExecutorContract] = {}
        self._adapters: dict[str, ExecutionAdapterSpec] = {}
        self._profiles: dict[str, ResultProfileSpec] = {}
        self._checks: dict[str, CheckSpec] = {}
        self._recoveries: dict[str, RecoverySpec] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_executor(self, contract: ExecutorContract) -> None:
        """Register (or replace) an executor contract by capability."""
        if not isinstance(contract, ExecutorContract):
            raise DomainError("contract must be an ExecutorContract")
        self._executors[contract.capability] = contract

    def register_adapter(self, adapter: ExecutionAdapterSpec) -> None:
        """Register (or replace) an execution adapter by name."""
        if not isinstance(adapter, ExecutionAdapterSpec):
            raise DomainError("adapter must be an ExecutionAdapterSpec")
        self._adapters[adapter.name] = adapter

    def register_profile(self, profile: ResultProfileSpec) -> None:
        """Register (or replace) a result profile by name."""
        if not isinstance(profile, ResultProfileSpec):
            raise DomainError("profile must be a ResultProfileSpec")
        self._profiles[profile.name] = profile

    def register_check(self, check: CheckSpec) -> None:
        """Register (or replace) a scientific check by name."""
        if not isinstance(check, CheckSpec):
            raise DomainError("check must be a CheckSpec")
        self._checks[check.name] = check

    def register_recovery(self, recovery: RecoverySpec) -> None:
        """Register (or replace) a recovery profile by name."""
        if not isinstance(recovery, RecoverySpec):
            raise DomainError("recovery must be a RecoverySpec")
        self._recoveries[recovery.name] = recovery

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
            return self._executors[capability]
        except KeyError as exc:
            raise RegistryLookupError(f"unknown executor capability: {capability!r}") from exc

    def find_executor(self, capability: ExecutorCapability) -> ExecutorContract | None:
        """Return the contract for *capability*, or ``None``."""
        return self._executors.get(capability)

    def adapter(self, name: str) -> ExecutionAdapterSpec:
        """Return the adapter named *name*.

        Raises
        ------
        RegistryLookupError
            Raised when the adapter is not registered.
        """
        try:
            return self._adapters[name]
        except KeyError as exc:
            raise RegistryLookupError(f"unknown execution adapter: {name!r}") from exc

    def find_adapter(self, name: str) -> ExecutionAdapterSpec | None:
        """Return the adapter named *name*, or ``None``."""
        return self._adapters.get(name)

    def profile(self, name: str) -> ResultProfileSpec:
        """Return the result profile named *name*.

        Raises
        ------
        RegistryLookupError
            Raised when the profile is not registered.
        """
        try:
            return self._profiles[name]
        except KeyError as exc:
            raise RegistryLookupError(f"unknown result profile: {name!r}") from exc

    def find_profile(self, name: str) -> ResultProfileSpec | None:
        """Return the result profile named *name*, or ``None``."""
        return self._profiles.get(name)

    def check(self, name: str) -> CheckSpec:
        """Return the scientific check named *name*.

        Raises
        ------
        RegistryLookupError
            Raised when the check is not registered.
        """
        try:
            return self._checks[name]
        except KeyError as exc:
            raise RegistryLookupError(f"unknown scientific check: {name!r}") from exc

    def find_check(self, name: str) -> CheckSpec | None:
        """Return the scientific check named *name*, or ``None``."""
        return self._checks.get(name)

    def recovery(self, name: str) -> RecoverySpec:
        """Return the recovery profile named *name*.

        Raises
        ------
        RegistryLookupError
            Raised when the recovery profile is not registered.
        """
        try:
            return self._recoveries[name]
        except KeyError as exc:
            raise RegistryLookupError(f"unknown recovery profile: {name!r}") from exc

    def find_recovery(self, name: str) -> RecoverySpec | None:
        """Return the recovery profile named *name*, or ``None``."""
        return self._recoveries.get(name)

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
    return (
        _standard_profile(checks),
        ResultProfileSpec(
            name="path_endpoints",
            contract_version="confflow.contract.result_profile.path_endpoints.v1",
            supported_checks=(
                "normal_termination",
                "geometry_required",
                "max_rmsd_from_input",
                "bond_drift",
            ),
            provides_structures=True,
            provides_results=True,
            provides_artifacts=True,
            description="Reaction-path endpoints (IRC/NEB): no frequency checks.",
        ),
        ResultProfileSpec(
            name="ensemble",
            contract_version="confflow.contract.result_profile.ensemble.v1",
            supported_checks=("normal_termination", "geometry_required"),
            provides_structures=True,
            provides_results=True,
            provides_artifacts=True,
            description="Conformer ensemble: many structures, energy results.",
        ),
        ResultProfileSpec(
            name="opaque",
            contract_version="confflow.contract.result_profile.opaque.v1",
            supported_checks=(),
            provides_structures=False,
            provides_results=False,
            provides_artifacts=True,
            description="Opaque native output: artifacts only, no parsed values.",
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
        ExecutionAdapterSpec(
            name="named_structures",
            contract_version="confflow.contract.adapter.named_structures.v1",
            capability=ExecutorCapability.CALCULATION,
            input_ports=(
                _structure_port(
                    "reactant",
                    Cardinality.ONE,
                    Pairing.BY_GROUP_KEY,
                    "Reactant structure of the reaction (QST2/QST3/NEB).",
                ),
                _structure_port(
                    "product",
                    Cardinality.ONE,
                    Pairing.BY_GROUP_KEY,
                    "Product structure of the reaction (QST2/QST3/NEB).",
                ),
                _structure_port(
                    "guess",
                    Cardinality.OPTIONAL,
                    Pairing.BY_GROUP_KEY,
                    "Optional transition-state guess (QST3).",
                ),
            ),
            description="Named-structure calculations paired by explicit group key.",
        ),
        ExecutionAdapterSpec(
            name="native_template",
            contract_version="confflow.contract.adapter.native_template.v1",
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
            description="Native template input rendering (V4-2); ports mirror standard.",
        ),
    )


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
            contract_version="confflow.contract.executor.confgen.v1",
            input_ports=(
                _structure_port(
                    "structure",
                    Cardinality.ONE,
                    Pairing.PER_STRUCTURE,
                    "Seed structure to generate conformers for.",
                ),
            ),
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
                    ("ensemble_report",),
                    "Conformer ensemble report artifacts.",
                ),
            ),
            stochastic=True,
            description="Stochastic conformer generation; requires an explicit seed.",
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
        ExecutorContract(
            capability=ExecutorCapability.ANALYSIS,
            contract_version="confflow.contract.executor.analysis.v1",
            input_ports=(
                _structure_port(
                    "structure",
                    Cardinality.OPTIONAL,
                    Pairing.SINGLE,
                    "Optional subject structure for the analysis.",
                ),
                _structure_port(
                    "structures",
                    Cardinality.MANY,
                    Pairing.SINGLE,
                    "Subject structures for the analysis (for example path endpoints).",
                ),
                _structure_port(
                    "ts_structures",
                    Cardinality.MANY,
                    Pairing.SINGLE,
                    "Transition-state structures referenced by endpoint parent links.",
                ),
                _result_port("results", Cardinality.MANY, Pairing.SINGLE),
                _result_port("ts_results", Cardinality.MANY, Pairing.SINGLE),
            ),
            output_ports=(
                _result_port("results", Cardinality.MANY, Pairing.BY_SUBJECT),
                _artifact_port(
                    "artifacts",
                    Cardinality.MANY,
                    Pairing.SINGLE,
                    ("report",),
                    "Analysis report artifacts.",
                ),
            ),
            description="Analysis over structures, results, or artifacts.",
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
    """Build a fresh registry populated with the built-in V4-1 vocabulary."""
    registry = ExecutionRegistry()
    for contract in _default_executors():
        registry.register_executor(contract)
    for adapter in _default_adapters():
        registry.register_adapter(adapter)
    checks = tuple(check.name for check in _default_checks())
    for check in _default_checks():
        registry.register_check(check)
    for profile in _default_profiles(checks):
        registry.register_profile(profile)
    for recovery in _default_recoveries():
        registry.register_recovery(recovery)
    return registry


@lru_cache(maxsize=1)
def default_registry() -> ExecutionRegistry:
    """Return the shared default registry.

    The returned registry is treated as read-only by the engine; callers that
    need to extend or override capabilities build their own registry with
    :func:`build_default_registry`.
    """
    return build_default_registry()
