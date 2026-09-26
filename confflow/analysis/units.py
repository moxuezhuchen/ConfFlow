#!/usr/bin/env python3

"""Energy unit projections for V4-6 Gibbs/PES analysis.

All V4-6 aggregation arithmetic runs in Hartree internally; this module
provides the only sanctioned unit projections.  Display conversions never
touch result identity: they map floats to floats and leave the source
:class:`~confflow.domain.result.ScientificResult` untouched.

Conversion factors (CODATA 2018, Tiesinga et al., Rev. Mod. Phys. 93,
025010 (2021): Hartree energy ``E_h = 4.3597447222071e-18 J``,
Avogadro constant ``N_A = 6.02214076e23 mol^-1`` (exact),
elementary charge ``e = 1.602176634e-19 J/eV`` (exact),
thermochemical calorie ``1 cal = 4.184 J`` (exact)):

- ``1 hartree = E_h * N_A / 1000 = 2625.4996394799 kJ/mol``
- ``1 hartree = 2625.4996394799 / 4.184 = 627.5094740631 kcal/mol``
- ``1 hartree = E_h / e = 27.211386245988 eV``

The domain layer (:mod:`confflow.domain.units`) declares the accepted
units and their quantity kinds but deliberately carries no conversion
factors; the factors above were verified against that module (same
:class:`~confflow.domain.units.Unit` members, ``QuantityKind.ENERGY``
membership for hartree/kcal/mol/kJ/mol/eV, canonical energy unit
Hartree) rather than copied from it.
"""

from __future__ import annotations

import math
from numbers import Real
from typing import Any, Final

from ..domain.errors import DomainError
from ..domain.result import ScientificResult
from ..domain.units import UNIT_QUANTITIES, QuantityKind, Unit

__all__ = [
    "CODE_ENERGY_MISSING",
    "CODE_CORRECTION_MISSING",
    "CODE_NON_NUMERIC_VALUE",
    "CODE_UNIT_MISMATCH",
    "AnalysisMathError",
    "from_hartree",
    "to_hartree",
    "value_in_hartree",
]

#: Error code when a required energy result is absent.
CODE_ENERGY_MISSING: Final[str] = "energy_missing"

#: Error code when a required Gibbs correction result is absent.
CODE_CORRECTION_MISSING: Final[str] = "correction_missing"

#: Error code when a result carries an incompatible unit or quantity.
CODE_UNIT_MISMATCH: Final[str] = "unit_mismatch"

#: Error code when a result value is not a finite real number.
CODE_NON_NUMERIC_VALUE: Final[str] = "non_numeric_value"

#: Hartree per kJ/mol reciprocal base: ``1 hartree = ... kJ/mol`` (CODATA 2018).
HARTREE_TO_KJ_PER_MOL: Final[float] = 2625.4996394799

#: ``1 hartree = ... kcal/mol`` (thermochemical calorie, exact 4.184 J).
HARTREE_TO_KCAL_PER_MOL: Final[float] = 627.5094740631

#: ``1 hartree = ... eV`` (CODATA 2018).
HARTREE_TO_EV: Final[float] = 27.211386245988

_HARTREE_PER_UNIT: Final[dict[Unit, float]] = {
    Unit.HARTREE: 1.0,
    Unit.KILOJOULE_PER_MOLE: 1.0 / HARTREE_TO_KJ_PER_MOL,
    Unit.KILOCALORIE_PER_MOLE: 1.0 / HARTREE_TO_KCAL_PER_MOL,
    Unit.ELECTRONVOLT: 1.0 / HARTREE_TO_EV,
}


class AnalysisMathError(DomainError):
    """Typed failure for V4-6 analysis arithmetic.

    Parameters
    ----------
    code : str
        Machine-readable code (``energy_missing``, ``correction_missing``,
        ``unit_mismatch``, ``non_numeric_value``, ...).
    message : str
        Human-readable message.
    subject_structure_id : str or None
        Structure the failed computation belonged to, when known.
    details : Mapping or None
        Additional machine-readable detail.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        subject_structure_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.subject_structure_id = subject_structure_id
        self.details = dict(details or {})


def _require_energy_unit(unit: Any, *, subject_structure_id: str | None) -> Unit:
    """Return *unit* when it measures energy, else raise a typed error."""
    if not isinstance(unit, Unit) or UNIT_QUANTITIES.get(unit) is not QuantityKind.ENERGY:
        raise AnalysisMathError(
            CODE_UNIT_MISMATCH,
            f"expected an energy unit, got {unit!r}",
            subject_structure_id=subject_structure_id,
            details={"unit": str(unit)},
        )
    return unit


def _require_finite(value: Any, *, subject_structure_id: str | None) -> float:
    """Return *value* as a finite float, else raise a typed error."""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise AnalysisMathError(
            CODE_NON_NUMERIC_VALUE,
            f"expected a real number, got {type(value).__name__}",
            subject_structure_id=subject_structure_id,
        )
    number = float(value)
    if not math.isfinite(number):
        raise AnalysisMathError(
            CODE_NON_NUMERIC_VALUE,
            "expected a finite number",
            subject_structure_id=subject_structure_id,
        )
    return number


def to_hartree(value: float, unit: Unit) -> float:
    """Project *value* expressed in *unit* into Hartree.

    Parameters
    ----------
    value : float
        Energy magnitude in *unit*.
    unit : Unit
        An energy unit (hartree, kJ/mol, kcal/mol, eV).

    Returns
    -------
    float
        The same energy in Hartree.

    Raises
    ------
    AnalysisMathError
        With code ``unit_mismatch`` when *unit* does not measure energy,
        or ``non_numeric_value`` when *value* is not finite.
    """
    energy_unit = _require_energy_unit(unit, subject_structure_id=None)
    magnitude = _require_finite(value, subject_structure_id=None)
    return magnitude * _HARTREE_PER_UNIT[energy_unit]


def from_hartree(value_hartree: float, unit: Unit) -> float:
    """Project a Hartree *value* into *unit* for display.

    Parameters
    ----------
    value_hartree : float
        Energy magnitude in Hartree.
    unit : Unit
        Target energy unit (typically kJ/mol or kcal/mol for display).

    Returns
    -------
    float
        The same energy expressed in *unit*.  The source result is never
        modified; identity digests are untouched.

    Raises
    ------
    AnalysisMathError
        With code ``unit_mismatch`` when *unit* does not measure energy,
        or ``non_numeric_value`` when *value_hartree* is not finite.
    """
    energy_unit = _require_energy_unit(unit, subject_structure_id=None)
    magnitude = _require_finite(value_hartree, subject_structure_id=None)
    return magnitude / _HARTREE_PER_UNIT[energy_unit]


def value_in_hartree(result: ScientificResult) -> float:
    """Return the energy value of *result* projected into Hartree.

    Parameters
    ----------
    result : ScientificResult
        Result whose quantity must be energy with a compatible unit.

    Returns
    -------
    float
        The result value in Hartree.

    Raises
    ------
    AnalysisMathError
        With code ``unit_mismatch`` when the result quantity is not
        energy or its unit is incompatible, or ``non_numeric_value``
        when the value is not a finite real number.  Bare floats are
        never accepted: unit safety is verified before any arithmetic.
    """
    subject = result.subject_structure_id
    if result.quantity is not QuantityKind.ENERGY:
        raise AnalysisMathError(
            CODE_UNIT_MISMATCH,
            f"expected quantity energy, got {result.quantity!r}",
            subject_structure_id=subject,
            details={"kind": result.kind},
        )
    _require_energy_unit(result.unit, subject_structure_id=subject)
    return to_hartree(_require_finite(result.value, subject_structure_id=subject), result.unit)
