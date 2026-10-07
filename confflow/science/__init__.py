#!/usr/bin/env python3

"""ConfFlow V4 pure science package.

Numeric geometry/graph algorithms for V4 execution; no dependency on
legacy orchestration (no `blocks.confgen`/`blocks.refine`, XYZ I/O,
progress UI). Allowed: stdlib, NumPy, lazy SciPy, `confflow.domain`
element data, in-package `bonding` via `confflow.science.bonds`.
Torsion mechanics from pure-numpy `blocks.confgen` subset; clustering
from pure `blocks.refine` subset; each function cites its source.
"""

from __future__ import annotations

from .bonds import covalent_radii, perceive_adjacency
from .cluster import kabsch_rmsd
from .torsion import (
    bfs_distances,
    edge_in_cycle,
    rotate_atoms_around_bond,
    topological_distance_matrix,
)

__all__ = [
    "bfs_distances",
    "covalent_radii",
    "edge_in_cycle",
    "kabsch_rmsd",
    "perceive_adjacency",
    "rotate_atoms_around_bond",
    "topological_distance_matrix",
]
