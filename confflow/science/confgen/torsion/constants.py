#!/usr/bin/env python3

"""Torsion lightweight constants (FIX-1A A5, stdlib only).

Single authority for the torsion vocabularies needed by schema/contract.
Values match the kernel ``planner`` vocabularies (``==``, not ``is``,
because kernel files must not import component modules per G13); the
component ``stage.py`` backend label is re-exported ``is`` identical
from here.
"""

from __future__ import annotations

import math

__all__ = [
    "BACKEND_NAME",
    "TORSION_MODELS",
    "TREATMENTS",
    "wrap_degrees",
]

#: Backend label stamped on every torsion realization result.
BACKEND_NAME: str = "geometric-rodrigues-v3"

#: Torsion sampling models (matches ``planner._TORSION_MODELS`` values).
TORSION_MODELS: tuple[str, ...] = (
    "relative_rotation_grid",
    "absolute_dihedral_grid",
    "chemical",
)

#: Supported treatments (matches ``planner._TREATMENTS`` values).
TREATMENTS: tuple[str, ...] = ("enumerate", "preserve_input")


def wrap_degrees(angle: float) -> float:
    """Wrap an angle in degrees into (-180, 180] (stdlib only).

    Same arithmetic as ``torsion.measure.wrap_degrees`` (``math.fmod``
    convention) without importing the NumPy-backed measure module, so
    schema validators stay free of solver imports.
    """
    wrapped = math.fmod(float(angle) + 180.0, 360.0)
    if wrapped <= 0.0:
        wrapped += 360.0
    return wrapped - 180.0
