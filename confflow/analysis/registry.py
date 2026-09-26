#!/usr/bin/env python3

"""Analysis capability registry for ConfFlow Workflow V4 (V4-6).

This module is the single source of truth for the closed V4-6 analysis
vocabulary: which capability names exist, which contract version each
carries, and which params each supports (energy-model modes, endpoint
assignment roles, partial policies).  There are no task enums here and
never will be: capabilities dispatch by ``(name, contract_version)``,
and endpoint chemistry flows through the explicit
``endpoint_assignment`` mapping on the definition.

The energy-model mode/fallback literals mirror the energy workstream's
policy vocabulary (``confflow.analysis.thermochemistry``, agent B
owned, authoritative); this registry only advertises them so editors
and validators can check capability params without importing the
energy math.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from ..domain._immutable import FrozenDict
from ..domain.binding import PartialConsumption
from ..domain.diagnostics import Diagnostic, DiagnosticSeverity
from .models import (
    CHEMISTRY_ROLES,
    ENDPOINT_SLOTS,
    AnalysisDefinition,
    AnalysisError,
)

__all__ = [
    "ANALYSIS_CAPABILITIES",
    "ANALYSIS_CAPABILITY_INDEX",
    "CHEMISTRY_ASSIGNMENT_ROLES",
    "ENERGY_MODEL_FALLBACKS",
    "ENERGY_MODEL_MODES",
    "PARTIAL_POLICIES",
    "REACTION_PROFILE_CAPABILITY",
    "REACTION_PROFILE_CONTRACT_VERSION",
    "AnalysisCapabilitySpec",
    "capabilities",
    "find_analysis_capability",
    "require_analysis_capability",
    "validate_analysis_definition",
]

#: Capability name of per-reaction Gibbs/PES aggregation.
REACTION_PROFILE_CAPABILITY: Final[str] = "reaction_profile"

#: Contract version of the ``reaction_profile`` capability.
REACTION_PROFILE_CONTRACT_VERSION: Final[str] = "confflow.contract.analysis.reaction_profile.v1"

#: Supported energy-model modes (advertised; the energy workstream owns them).
ENERGY_MODEL_MODES: Final[tuple[str, ...]] = ("direct", "composite")

#: Supported energy-model fallbacks (advertised; the energy workstream owns them).
ENERGY_MODEL_FALLBACKS: Final[tuple[str, ...]] = ("none", "low_level")

#: Chemistry roles an endpoint slot may be assigned to.
CHEMISTRY_ASSIGNMENT_ROLES: Final[tuple[str, ...]] = CHEMISTRY_ROLES

#: Supported partial policies, mirroring :class:`PartialConsumption` values.
PARTIAL_POLICIES: Final[tuple[str, ...]] = tuple(item.value for item in PartialConsumption)


@dataclass(frozen=True, slots=True)
class AnalysisCapabilitySpec:
    """Descriptor of one analysis capability.

    Parameters
    ----------
    name : str
        Capability name used as ``AnalysisDefinition.kind``.
    contract_version : str
        Per-capability version string folded into semantic digests.
    description : str
        Human-readable description.
    energy_model_modes : tuple[str, ...]
        Advertised energy-model modes.
    energy_model_fallbacks : tuple[str, ...]
        Advertised energy-model fallbacks.
    assignment_roles : tuple[str, ...]
        Allowed ``endpoint_assignment`` values per slot.
    partial_policies : tuple[str, ...]
        Allowed partial policies.
    params_schema : FrozenDict
        Supported params schema (energy-model modes, assignment,
        partial policy); informational, never a task dispatch.
    """

    name: str
    contract_version: str
    params_schema: FrozenDict
    description: str = ""
    energy_model_modes: tuple[str, ...] = ENERGY_MODEL_MODES
    energy_model_fallbacks: tuple[str, ...] = ENERGY_MODEL_FALLBACKS
    assignment_roles: tuple[str, ...] = CHEMISTRY_ASSIGNMENT_ROLES
    partial_policies: tuple[str, ...] = PARTIAL_POLICIES

    def __post_init__(self) -> None:
        """Validate the descriptor explicitly."""
        for field_name in ("name", "contract_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise AnalysisError(
                    "analysis_invalid_definition",
                    f"capability {field_name} must be a non-empty string",
                    details={"reason": "invalid_capability", "field": field_name},
                )
        for field_name in (
            "energy_model_modes",
            "energy_model_fallbacks",
            "assignment_roles",
            "partial_policies",
        ):
            values = tuple(getattr(self, field_name))
            if not values:
                raise AnalysisError(
                    "analysis_invalid_definition",
                    f"capability {field_name} must not be empty",
                    details={"reason": "invalid_capability", "field": field_name},
                )
            object.__setattr__(self, field_name, values)
        if not isinstance(self.params_schema, FrozenDict):
            object.__setattr__(self, "params_schema", FrozenDict(dict(self.params_schema)))

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "name": self.name,
            "contract_version": self.contract_version,
            "description": self.description,
            "energy_model_modes": list(self.energy_model_modes),
            "energy_model_fallbacks": list(self.energy_model_fallbacks),
            "assignment_roles": list(self.assignment_roles),
            "partial_policies": list(self.partial_policies),
            "params_schema": self.params_schema.thaw(),
        }


def _reaction_profile_spec() -> AnalysisCapabilitySpec:
    """Build the ``reaction_profile`` capability descriptor."""
    return AnalysisCapabilitySpec(
        name=REACTION_PROFILE_CAPABILITY,
        contract_version=REACTION_PROFILE_CONTRACT_VERSION,
        description=(
            "Per-reaction Gibbs/PES aggregation over one transition state "
            "plus native forward/reverse path endpoints."
        ),
        params_schema=FrozenDict(
            {
                "kind": {"const": REACTION_PROFILE_CAPABILITY},
                "energy_model": {
                    "mode": list(ENERGY_MODEL_MODES),
                    "fallback": list(ENERGY_MODEL_FALLBACKS),
                    "selectors": (
                        "exact result-kind strings naming the electronic "
                        "energy and Gibbs correction (no cross-kind guessing)"
                    ),
                },
                "endpoint_assignment": {
                    slot: list(CHEMISTRY_ASSIGNMENT_ROLES) for slot in ENDPOINT_SLOTS
                },
                "partial_policy": list(PARTIAL_POLICIES),
            }
        ),
    )


#: Closed V4-6 analysis vocabulary, in deterministic order.
ANALYSIS_CAPABILITIES: Final[tuple[AnalysisCapabilitySpec, ...]] = (_reaction_profile_spec(),)

#: Capability index by name for main-agent wiring.
ANALYSIS_CAPABILITY_INDEX: Final[FrozenDict] = FrozenDict(
    {spec.name: spec for spec in ANALYSIS_CAPABILITIES}
)


def capabilities() -> tuple[dict[str, str], ...]:
    """Return the frozen ``(capability, contract_version)`` pairs.

    This is the prefer-real seam consumed by the cross-workstream
    contract tests: each entry carries exactly ``"capability"`` and
    ``"contract_version"`` so producers and consumers compare literal
    strings instead of importing each other's descriptors.

    Returns
    -------
    tuple[dict[str, str], ...]
        One mapping per registered capability, in registry order.
    """
    return tuple(
        {"capability": spec.name, "contract_version": spec.contract_version}
        for spec in ANALYSIS_CAPABILITIES
    )


def find_analysis_capability(name: str) -> AnalysisCapabilitySpec | None:
    """Return the capability named *name*, or ``None``.

    Parameters
    ----------
    name : str
        Capability name to look up.

    Returns
    -------
    AnalysisCapabilitySpec or None
        The descriptor, or ``None`` when unregistered.
    """
    found = ANALYSIS_CAPABILITY_INDEX.get(name)
    return found if isinstance(found, AnalysisCapabilitySpec) else None


def require_analysis_capability(name: str) -> AnalysisCapabilitySpec:
    """Return the capability named *name*, failing closed when unknown.

    Parameters
    ----------
    name : str
        Capability name to look up.

    Returns
    -------
    AnalysisCapabilitySpec
        The descriptor.

    Raises
    ------
    AnalysisError
        With code ``analysis_unknown_kind`` when *name* is unregistered.
    """
    found = find_analysis_capability(name)
    if found is None:
        raise AnalysisError(
            "analysis_unknown_kind",
            f"unknown analysis capability: {name!r}",
            details={
                "reason": "unknown_kind",
                "kind": name,
                "known": sorted(ANALYSIS_CAPABILITY_INDEX.keys()),
            },
        )
    return found


def validate_analysis_definition(definition: AnalysisDefinition) -> tuple[Diagnostic, ...]:
    """Pre-flight check of a definition against the registry.

    Parameters
    ----------
    definition : AnalysisDefinition
        Definition to check (construction already enforces assignment
        and policy shape; this checks capability registration).

    Returns
    -------
    tuple[Diagnostic, ...]
        Empty when the kind is registered, else one error diagnostic
        with code ``analysis_unknown_kind``.

    Raises
    ------
    AnalysisError
        With code ``analysis_invalid_definition`` when *definition* is
        not an :class:`AnalysisDefinition`.
    """
    if not isinstance(definition, AnalysisDefinition):
        raise AnalysisError(
            "analysis_invalid_definition",
            "validation requires an AnalysisDefinition",
            details={"reason": "invalid_definition_type"},
        )
    if find_analysis_capability(definition.kind) is None:
        return (
            Diagnostic(
                code="analysis_unknown_kind",
                message=f"unknown analysis capability: {definition.kind!r}",
                severity=DiagnosticSeverity.ERROR,
                details=FrozenDict(
                    {
                        "reason": "unknown_kind",
                        "kind": definition.kind,
                        "known": sorted(ANALYSIS_CAPABILITY_INDEX.keys()),
                    }
                ),
            ),
        )
    return ()
