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
    "measure_dihedral",
    "parse_path_declarations",
    "resolve_paths",
    "topology_digest_of",
    "wrap_degrees",
]
