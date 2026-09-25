#!/usr/bin/env python3

"""Canonical physical units and quantity kinds for V4 scientific results.

The V4 domain layer carries units explicitly on every quantity.  Canonical
internal units are fixed here so results produced by different programs can be
compared without guessing:

- geometry: Ångström
- energy: Hartree
- frequency: cm^-1
- dipole moment: Debye (explicitly declared until a canonical policy lands)
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType
from typing import Final

__all__ = [
    "CANONICAL_UNITS",
    "UNIT_QUANTITIES",
    "QuantityKind",
    "Unit",
    "is_canonical_unit",
]


class QuantityKind(str, Enum):
    """Scientific quantity kinds carried by V4 results."""

    GEOMETRY = "geometry"
    ENERGY = "energy"
    FREQUENCY = "frequency"
    DIPOLE_MOMENT = "dipole_moment"


class Unit(str, Enum):
    """Physical units accepted by V4 result payloads."""

    ANGSTROM = "angstrom"
    BOHR = "bohr"
    HARTREE = "hartree"
    KILOCALORIE_PER_MOLE = "kcal/mol"
    KILOJOULE_PER_MOLE = "kJ/mol"
    ELECTRONVOLT = "eV"
    CM_INVERSE = "cm^-1"
    DEBYE = "debye"


#: Unit accepted for each quantity kind when no external unit is declared.
CANONICAL_UNITS: Final[Mapping[QuantityKind, Unit]] = MappingProxyType(
    {
        QuantityKind.GEOMETRY: Unit.ANGSTROM,
        QuantityKind.ENERGY: Unit.HARTREE,
        QuantityKind.FREQUENCY: Unit.CM_INVERSE,
        QuantityKind.DIPOLE_MOMENT: Unit.DEBYE,
    }
)

#: Quantity kind each unit belongs to; used to reject unit/kind mismatches.
UNIT_QUANTITIES: Final[Mapping[Unit, QuantityKind]] = MappingProxyType(
    {
        Unit.ANGSTROM: QuantityKind.GEOMETRY,
        Unit.BOHR: QuantityKind.GEOMETRY,
        Unit.HARTREE: QuantityKind.ENERGY,
        Unit.KILOCALORIE_PER_MOLE: QuantityKind.ENERGY,
        Unit.KILOJOULE_PER_MOLE: QuantityKind.ENERGY,
        Unit.ELECTRONVOLT: QuantityKind.ENERGY,
        Unit.CM_INVERSE: QuantityKind.FREQUENCY,
        Unit.DEBYE: QuantityKind.DIPOLE_MOMENT,
    }
)


def is_canonical_unit(quantity: QuantityKind, unit: Unit) -> bool:
    """Return whether *unit* is the canonical unit for *quantity*."""
    return CANONICAL_UNITS.get(quantity) is unit
