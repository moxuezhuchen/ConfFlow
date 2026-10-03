#!/usr/bin/env python3

"""ConfGen v3 torsion subpackage (CORE lane).

Stage, dihedral measurement, and path resolution.
Wraps :mod:`confflow.science.torsion` primitives without rewriting them.
"""

from __future__ import annotations

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
    canonical_grid_size,
    canonicalize_rotors,
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
    "canonical_grid_size",
    "canonicalize_rotors",
    "measure_dihedral",
    "parse_path_declarations",
    "resolve_paths",
    "topology_digest_of",
    "wrap_degrees",
]
