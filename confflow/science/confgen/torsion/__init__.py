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
    legacy_oriented_grid_geometries,
    legacy_rotatable_bonds,
    legacy_selection_fingerprint,
)
from confflow.science.confgen.torsion.measure import measure_dihedral, wrap_degrees
from confflow.science.confgen.torsion.paths import (
    PATH_AMBIGUOUS,
    PATH_CROSSES_RING,
    PATH_DIRECTION_CONFLICT,
    PATH_DISCONNECTED,
    PATH_INVALID_ENDPOINT,
    PATH_SHORT_BOND,
    PATH_SHORT_BOND_RATIO,
    ROTOR_SAMPLING_CONFLICT,
    WARNING_SHORT_BOND,
    CanonicalRotor,
    PathResolution,
    PathResolutionError,
    ResolvedPath,
    build_chain_rotors,
    canonical_grid_size,
    canonicalize_rotors,
    cut_component,
    parse_path_declarations,
    resolve_paths,
    topology_digest_of,
)
from confflow.science.confgen.torsion.stage import BACKEND_NAME, TorsionStage

__all__ = [
    "BACKEND_NAME",
    "PATH_AMBIGUOUS",
    "PATH_CROSSES_RING",
    "PATH_DIRECTION_CONFLICT",
    "PATH_DISCONNECTED",
    "PATH_INVALID_ENDPOINT",
    "PATH_SHORT_BOND",
    "PATH_SHORT_BOND_RATIO",
    "ROTOR_SAMPLING_CONFLICT",
    "WARNING_SHORT_BOND",
    "CanonicalRotor",
    "PathResolution",
    "PathResolutionError",
    "ResolvedPath",
    "TorsionStage",
    "build_chain_rotors",
    "canonical_grid_size",
    "canonicalize_rotors",
    "cut_component",
    "legacy_cap_v1",
    "legacy_grid_geometries",
    "legacy_oriented_grid_geometries",
    "legacy_rotatable_bonds",
    "legacy_selection_fingerprint",
    "iter_legacy_grid",
    "measure_dihedral",
    "parse_path_declarations",
    "resolve_paths",
    "topology_digest_of",
    "wrap_degrees",
]
