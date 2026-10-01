#!/usr/bin/env python3

"""ConfGen v3 perception audit (CORE lane).

Atom-order guard, rigid-integrity checks, full parent-lock audit after each
realization, and drift-evidence routing. Measurement deviations on ancestor
axes are evidence/events on the descendant record (conformational coupling
is physical), never second terminal statuses; only atom-order and integrity
violations fail the lock audit itself. Stage perception stays lazy so this
module never hard-requires coordination/ring stages.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np

from confflow.domain.structure import StructureRecord

__all__ = [
    "audit_parent_locks",
    "check_atom_order",
    "drift_event",
    "max_bond_length_deviation",
]


def check_atom_order(structure: StructureRecord, context: Any) -> bool:
    """Return True iff output atom order matches the input authority."""
    return tuple(structure.atoms) == tuple(context.structure.atoms)


def max_bond_length_deviation(
    coords: np.ndarray, reference: np.ndarray, adjacency: Sequence[Sequence[int]]
) -> float:
    """Return the maximum bonded-pair distance deviation (Angstrom)."""
    worst = 0.0
    for first, row in enumerate(adjacency):
        for second in row:
            if second <= first:
                continue
            current = float(np.linalg.norm(coords[first] - coords[second]))
            expected = float(np.linalg.norm(reference[first] - reference[second]))
            deviation = abs(current - expected)
            if deviation > worst:
                worst = deviation
    return worst


def drift_event(
    *,
    axis: str,
    detail: str,
    expected: Any = None,
    measured: Any = None,
    tolerance: Any = None,
) -> dict[str, Any]:
    """Build a drift-evidence payload (evidence, never a terminal status)."""
    payload: dict[str, Any] = {"kind": "drift", "axis": str(axis), "detail": str(detail)}
    if expected is not None:
        payload["expected"] = expected
    if measured is not None:
        payload["measured"] = measured
    if tolerance is not None:
        payload["tolerance"] = tolerance
    return payload


def audit_parent_locks(
    structure: StructureRecord,
    ancestors: Sequence[Any],
    context: Any,
    *,
    parent: Any,
    check_bond_integrity: bool = True,
) -> tuple[bool, list[dict[str, Any]]]:
    """Audit structural locks on a freshly realized structure.

    Hard checks: atom-order guard, finite coordinates, atom-count stability,
    and -- only when ``check_bond_integrity`` holds -- rigid bond-length
    integrity against the immediate PARENT geometry (torsion rigid-rotation
    rule; the engine enables it only for torsion-axis realizations, never
    for ring/coordination solver outputs). Upstream AXIS-state preservation
    (all C/R/T locks) is the engine's strict job via stage hooks
    (`verify_locked` / tolerance-aware matchers); this function returns only
    structural evidence and never axis drift verdicts. ``ancestors`` is
    retained for signature compatibility and ignored.
    """
    evidence: list[dict[str, Any]] = []
    if not check_atom_order(structure, context):
        return False, [
            {
                "kind": "atom_order_violation",
                "detail": "output atom order differs from input authority",
            }
        ]
    coords = np.asarray(structure.coordinates, dtype=np.float64)
    parent_coords = np.asarray(parent.structure.coordinates, dtype=np.float64)
    if coords.shape != parent_coords.shape:
        return False, [{"kind": "atom_count_violation", "detail": "atom count changed"}]
    if not np.all(np.isfinite(coords)):
        return False, [
            {"kind": "nonfinite_coordinates", "detail": "realized coordinates not finite"}
        ]
    if check_bond_integrity:
        deviation = max_bond_length_deviation(coords, parent_coords, context.adjacency)
        tolerance = float(context.tolerances.bond_length_atol)
        if not math.isfinite(deviation) or deviation > tolerance:
            return False, [
                {
                    "kind": "integrity_violation",
                    "detail": "bond-length integrity outside torsion rigid-rotation tolerance",
                    "measured": float(deviation),
                    "tolerance": tolerance,
                }
            ]
    return True, evidence
