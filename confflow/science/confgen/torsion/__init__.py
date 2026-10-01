#!/usr/bin/env python3

"""ConfGen v3 torsion subpackage (CORE lane).

Stage, dihedral measurement, and the explicitly versioned legacy adapter.
Wraps :mod:`confflow.science.torsion` primitives without rewriting them.
"""

from __future__ import annotations

from confflow.science.confgen.torsion.legacy import (
    iter_legacy_grid,
    legacy_cap_v1,
    legacy_grid_geometries,
    legacy_rotatable_bonds,
    legacy_selection_fingerprint,
)
from confflow.science.confgen.torsion.measure import measure_dihedral, wrap_degrees
from confflow.science.confgen.torsion.stage import BACKEND_NAME, TorsionStage

__all__ = [
    "BACKEND_NAME",
    "TorsionStage",
    "legacy_cap_v1",
    "legacy_grid_geometries",
    "legacy_rotatable_bonds",
    "legacy_selection_fingerprint",
    "iter_legacy_grid",
    "measure_dihedral",
    "wrap_degrees",
]
