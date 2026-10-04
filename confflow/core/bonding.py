#!/usr/bin/env python3

"""Compatibility forwarding shim.

The bond-perception implementation lives in :mod:`confflow.science.bonding`;
this module re-exports the same objects so historical
``confflow.core.bonding`` imports keep working against the single authority.
"""

from __future__ import annotations

from ..science.bonding import (
    MIN_BOND_DISTANCE_ANGSTROM,
    UnknownElementError,
    build_adjacency,
    covalent_radius,
    has_known_covalent_radii,
    infer_bond_pairs,
)

__all__ = [
    "MIN_BOND_DISTANCE_ANGSTROM",
    "UnknownElementError",
    "covalent_radius",
    "has_known_covalent_radii",
    "infer_bond_pairs",
    "build_adjacency",
]
