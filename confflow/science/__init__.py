#!/usr/bin/env python3

"""ConfFlow V4 pure science package (wave-1 stream C, exclusive).

Numeric geometry/graph algorithms owned by the V4 execution layer, with
no dependency on legacy orchestration entrypoints (no
``blocks.confgen`` generator/pools/CLI, no ``blocks.refine`` processor,
no XYZ file I/O, no progress UI).  Allowed dependencies: stdlib, NumPy,
SciPy (lazy), ``confflow.domain`` element data, and the in-package
``confflow.science.bonding`` bond-perception authority (reached through
:mod:`confflow.science.bonds`).

Provenance: torsion mechanics extracted from the pure-numpy subset of
``confflow.blocks.confgen`` (Rodrigues rotation, ring
refusal, clash rule); clustering mechanics extracted from the pure
subset of ``confflow.blocks.refine`` (bond rule, Kabsch formulation,
mapping-gated comparison rule).  Each function cites its source.
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
