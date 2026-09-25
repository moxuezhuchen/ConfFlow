#!/usr/bin/env python3

"""The single authority for scientific parameter precedence.

Precedence (highest first):

1. an explicit inherent property of the :class:`StructureRecord`
   (``charge`` / ``multiplicity``);
2. an explicit step scientific override;
3. an explicit run-level scientific default.

Conflicts between a structure property and a step override are never silent:
the step wins *and* a ``scientific_parameter_conflict`` warning records the
override.  Electron parity is validated when both charge and multiplicity are
known, because a mismatched multiplicity is chemically impossible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...domain._immutable import FrozenDict
from ...domain.diagnostics import Diagnostic
from ...domain.elements import atomic_number
from ...domain.errors import ElementSymbolError
from ...domain.structure import StructureRecord
from .diagnostics import DiagnosticCode, DiagnosticReason, error, warning

__all__ = [
    "EffectiveScientificParameters",
    "check_electron_parity",
    "resolve_scientific_parameters",
]


@dataclass(frozen=True, slots=True)
class EffectiveScientificParameters:
    """Effective science of one structure in one step."""

    charge: int | None
    multiplicity: int | None
    freeze: tuple[int, ...] | None = None
    charge_source: str | None = None
    multiplicity_source: str | None = None
    freeze_source: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """Return the digest contribution of the effective parameters."""
        return {
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "freeze": list(self.freeze) if self.freeze is not None else None,
        }


def _resolve_field(
    inherent: int | None,
    override: int | None,
    default: int | None,
) -> tuple[int | None, str | None]:
    if inherent is not None:
        source = "structure"
        value = inherent
    elif override is not None:
        source = "step_override"
        value = override
    elif default is not None:
        source = "run_default"
        value = default
    else:
        return None, None
    return value, source


def resolve_scientific_parameters(
    *,
    structure: StructureRecord,
    overrides: FrozenDict | dict[str, Any],
    defaults: Any,
) -> tuple[EffectiveScientificParameters, tuple[Diagnostic, ...]]:
    """Resolve effective charge/multiplicity for *structure* in one step.

    Parameters
    ----------
    structure : StructureRecord
        Structure whose inherent properties are the highest-priority source.
    overrides : FrozenDict | dict[str, Any]
        Explicit step scientific overrides.
    defaults : ScientificDefaults
        Explicit run-level scientific defaults.

    Returns
    -------
    tuple[EffectiveScientificParameters, tuple[Diagnostic, ...]]
        Effective parameters plus any conflict/parity diagnostics.
    """
    override_charge = overrides.get("charge")
    override_multiplicity = overrides.get("multiplicity")
    default_charge = getattr(defaults, "charge", None)
    default_multiplicity = getattr(defaults, "multiplicity", None)
    diagnostics: list[Diagnostic] = []
    charge, charge_source = _resolve_field(
        structure.charge,
        override_charge,
        default_charge,
    )
    if (
        structure.charge is not None
        and override_charge is not None
        and structure.charge != override_charge
    ):
        diagnostics.append(
            warning(
                DiagnosticCode.SCIENTIFIC_PARAMETER_CONFLICT,
                DiagnosticReason.STRUCTURE_VALUE_OVERRIDDEN,
                "step charge override replaces the structure's explicit charge",
                step_id=structure.source_step_id,
                logical_key=structure.id,
                field_path="calculation.overrides.charge",
                details={
                    "structure_id": structure.id,
                    "structure_value": structure.charge,
                    "override_value": override_charge,
                },
            )
        )
    multiplicity, multiplicity_source = _resolve_field(
        structure.multiplicity,
        override_multiplicity,
        default_multiplicity,
    )
    override_freeze = overrides.get("freeze")
    default_freeze = getattr(defaults, "freeze", None)
    if override_freeze is not None:
        freeze: tuple[int, ...] | None = tuple(override_freeze)
        freeze_source: str | None = "step_override"
    elif default_freeze is not None:
        freeze = tuple(default_freeze)
        freeze_source = "run_default"
    else:
        freeze = None
        freeze_source = None
    if (
        structure.multiplicity is not None
        and override_multiplicity is not None
        and structure.multiplicity != override_multiplicity
    ):
        diagnostics.append(
            warning(
                DiagnosticCode.SCIENTIFIC_PARAMETER_CONFLICT,
                DiagnosticReason.STRUCTURE_VALUE_OVERRIDDEN,
                "step multiplicity override replaces the structure's explicit multiplicity",
                step_id=structure.source_step_id,
                logical_key=structure.id,
                field_path="calculation.overrides.multiplicity",
                details={
                    "structure_id": structure.id,
                    "structure_value": structure.multiplicity,
                    "override_value": override_multiplicity,
                },
            )
        )
    effective = EffectiveScientificParameters(
        charge=charge,
        multiplicity=multiplicity,
        freeze=freeze,
        charge_source=charge_source,
        multiplicity_source=multiplicity_source,
        freeze_source=freeze_source,
    )
    parity = check_electron_parity(structure, charge, multiplicity)
    if parity is not None:
        diagnostics.append(parity)
    if freeze:
        out_of_range = [index for index in freeze if index > len(structure.atoms)]
        if out_of_range:
            diagnostics.append(
                error(
                    DiagnosticCode.SCIENTIFIC_PARAMETER_CONFLICT,
                    DiagnosticReason.FREEZE_INDEX_OUT_OF_RANGE,
                    "freeze indices exceed the structure's atom count",
                    logical_key=structure.id,
                    field_path="calculation.overrides.freeze",
                    details={
                        "structure_id": structure.id,
                        "atoms": len(structure.atoms),
                        "indices": out_of_range,
                    },
                )
            )
    return effective, tuple(diagnostics)


def check_electron_parity(
    structure: StructureRecord, charge: int | None, multiplicity: int | None
) -> Diagnostic | None:
    """Return a diagnostic when charge/multiplicity are chemically impossible."""
    if charge is None or multiplicity is None:
        return None
    total_electrons = -charge
    for symbol in structure.atoms:
        try:
            total_electrons += atomic_number(symbol)
        except ElementSymbolError:
            return None
    if total_electrons < 0:
        return error(
            DiagnosticCode.SCIENTIFIC_PARAMETER_CONFLICT,
            DiagnosticReason.ELECTRON_PARITY_MISMATCH,
            "charge leaves a negative electron count",
            logical_key=structure.id,
            field_path="scientific_defaults.charge",
            details={"structure_id": structure.id, "charge": charge, "electrons": total_electrons},
        )
    if (multiplicity - 1) % 2 != total_electrons % 2:
        return error(
            DiagnosticCode.SCIENTIFIC_PARAMETER_CONFLICT,
            DiagnosticReason.ELECTRON_PARITY_MISMATCH,
            "multiplicity parity does not match the electron count",
            logical_key=structure.id,
            field_path="scientific_defaults.multiplicity",
            details={
                "structure_id": structure.id,
                "charge": charge,
                "multiplicity": multiplicity,
                "electrons": total_electrons,
            },
        )
    if multiplicity > total_electrons + 1:
        return error(
            DiagnosticCode.SCIENTIFIC_PARAMETER_CONFLICT,
            DiagnosticReason.ELECTRON_PARITY_MISMATCH,
            "multiplicity exceeds the maximum for the electron count",
            logical_key=structure.id,
            field_path="scientific_defaults.multiplicity",
            details={
                "structure_id": structure.id,
                "multiplicity": multiplicity,
                "electrons": total_electrons,
            },
        )
    return None
