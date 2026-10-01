#!/usr/bin/env python3

"""ConfGen v3 tolerance resolution (CORE lane).

Tolerances are scope (never state identity): they travel in the resolved
spec and the immutable context, never in :class:`ConfgenStateKey`.
Unknown tolerance keys fail closed.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["ConfgenTolerances", "resolve_tolerances"]


@dataclass(frozen=True, slots=True)
class ConfgenTolerances:
    """Numeric tolerances for realization integrity and perception audit.

    ``bond_length_atol`` is torsion rigid-rotation integrity ONLY (Rodrigues
    rotations preserve bond lengths to floating-point precision). Ring and
    coordination solvers use their own explicitly declared realistic
    geometric-constraint tolerances below; core never applies the 1e-6 bond
    check to non-torsion structures and never loosens checks to fit
    results. Ring/coordination fields are interpreted by those lanes.
    """

    bond_length_atol: float = 1e-6
    dihedral_atol_deg: float = 1.0
    clash_threshold: float = 0.65
    bond_scale: float = 1.15
    parent_lock_atol_deg: float = 1.0
    # Ring-lane owned geometric tolerances (R3): template idealization vs
    # input, rigid substituent propagation, local frame singularity floor.
    ring_bond_atol: float = 0.08
    substituent_bond_atol: float = 1e-6
    frame_det_min: float = 1e-8
    ring_angle_atol_deg: float = 5.0
    ring_torsion_atol_deg: float = 10.0
    coordination_bond_atol: float = 0.05
    coordination_angle_atol_deg: float = 3.0

    def __post_init__(self) -> None:
        for name in (
            "bond_length_atol",
            "dihedral_atol_deg",
            "clash_threshold",
            "bond_scale",
            "parent_lock_atol_deg",
            "ring_bond_atol",
            "substituent_bond_atol",
            "frame_det_min",
            "ring_angle_atol_deg",
            "ring_torsion_atol_deg",
            "coordination_bond_atol",
            "coordination_angle_atol_deg",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a number, got {value!r}")
            number = float(value)
            if not math.isfinite(number) or number <= 0:
                raise ValueError(f"{name} must be a positive finite number, got {value!r}")
            object.__setattr__(self, name, number)


_KNOWN_KEYS = (
    "bond_length_atol",
    "dihedral_atol_deg",
    "clash_threshold",
    "bond_scale",
    "parent_lock_atol_deg",
    "ring_bond_atol",
    "substituent_bond_atol",
    "frame_det_min",
    "ring_angle_atol_deg",
    "ring_torsion_atol_deg",
    "coordination_bond_atol",
    "coordination_angle_atol_deg",
)


def resolve_tolerances(spec: Mapping[str, Any] | None) -> ConfgenTolerances:
    """Resolve tolerances from ``spec["tolerances"]`` (fail closed).

    Unknown keys raise :class:`ValueError`; values must be positive finite
    numbers. ``None`` (or an empty mapping) yields defaults, which match
    the legacy executor (bond_scale 1.15, clash 0.65).
    """
    if spec is None:
        return ConfgenTolerances()
    if not isinstance(spec, Mapping):
        raise ValueError("tolerances must be a mapping")
    unknown = sorted(set(spec) - set(_KNOWN_KEYS))
    if unknown:
        raise ValueError(f"unknown tolerance keys {unknown}; allowed {list(_KNOWN_KEYS)}")
    return ConfgenTolerances(**{key: spec[key] for key in _KNOWN_KEYS if key in spec})
