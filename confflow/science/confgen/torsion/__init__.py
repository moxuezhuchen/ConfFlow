#!/usr/bin/env python3

"""ConfGen v3 torsion subpackage (CORE lane).

Stage, dihedral measurement, and path resolution.
Wraps :mod:`confflow.science.torsion` primitives without rewriting them.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .measure import measure_dihedral as measure_dihedral
    from .measure import wrap_degrees as wrap_degrees
    from .paths import (
        PATH_AMBIGUOUS as PATH_AMBIGUOUS,
    )
    from .paths import (
        PATH_CROSSES_RING as PATH_CROSSES_RING,
    )
    from .paths import (
        PATH_DIRECTION_CONFLICT as PATH_DIRECTION_CONFLICT,
    )
    from .paths import (
        PATH_DISCONNECTED as PATH_DISCONNECTED,
    )
    from .paths import (
        PATH_INVALID_ENDPOINT as PATH_INVALID_ENDPOINT,
    )
    from .paths import (
        PATH_SHORT_BOND as PATH_SHORT_BOND,
    )
    from .paths import (
        PATH_SHORT_BOND_RATIO as PATH_SHORT_BOND_RATIO,
    )
    from .paths import (
        ROTOR_SAMPLING_CONFLICT as ROTOR_SAMPLING_CONFLICT,
    )
    from .paths import (
        WARNING_SHORT_BOND as WARNING_SHORT_BOND,
    )
    from .paths import (
        CanonicalRotor as CanonicalRotor,
    )
    from .paths import (
        PathResolution as PathResolution,
    )
    from .paths import (
        PathResolutionError as PathResolutionError,
    )
    from .paths import (
        ResolvedPath as ResolvedPath,
    )
    from .paths import (
        canonical_grid_size as canonical_grid_size,
    )
    from .paths import (
        canonicalize_rotors as canonicalize_rotors,
    )
    from .paths import (
        parse_path_declarations as parse_path_declarations,
    )
    from .paths import (
        resolve_paths as resolve_paths,
    )
    from .paths import (
        topology_digest_of as topology_digest_of,
    )
    from .stage import BACKEND_NAME as BACKEND_NAME
    from .stage import TorsionStage as TorsionStage

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

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "BACKEND_NAME": (".stage", "BACKEND_NAME"),
    "PATH_AMBIGUOUS": (".paths", "PATH_AMBIGUOUS"),
    "PATH_CROSSES_RING": (".paths", "PATH_CROSSES_RING"),
    "PATH_DIRECTION_CONFLICT": (".paths", "PATH_DIRECTION_CONFLICT"),
    "PATH_DISCONNECTED": (".paths", "PATH_DISCONNECTED"),
    "PATH_INVALID_ENDPOINT": (".paths", "PATH_INVALID_ENDPOINT"),
    "PATH_SHORT_BOND": (".paths", "PATH_SHORT_BOND"),
    "PATH_SHORT_BOND_RATIO": (".paths", "PATH_SHORT_BOND_RATIO"),
    "ROTOR_SAMPLING_CONFLICT": (".paths", "ROTOR_SAMPLING_CONFLICT"),
    "WARNING_SHORT_BOND": (".paths", "WARNING_SHORT_BOND"),
    "CanonicalRotor": (".paths", "CanonicalRotor"),
    "PathResolution": (".paths", "PathResolution"),
    "PathResolutionError": (".paths", "PathResolutionError"),
    "ResolvedPath": (".paths", "ResolvedPath"),
    "TorsionStage": (".stage", "TorsionStage"),
    "canonical_grid_size": (".paths", "canonical_grid_size"),
    "canonicalize_rotors": (".paths", "canonicalize_rotors"),
    "measure_dihedral": (".measure", "measure_dihedral"),
    "parse_path_declarations": (".paths", "parse_path_declarations"),
    "resolve_paths": (".paths", "resolve_paths"),
    "topology_digest_of": (".paths", "topology_digest_of"),
    "wrap_degrees": (".measure", "wrap_degrees"),
}


def __getattr__(name: str) -> Any:
    export = _LAZY_EXPORTS.get(name)
    if export is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = export
    module = importlib.import_module(module_name, package=__name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
