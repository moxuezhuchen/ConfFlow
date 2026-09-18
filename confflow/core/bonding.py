#!/usr/bin/env python3

"""Coordinate-based bond perception shared across layers.

The bonding rule is intentionally simple and identical everywhere:

    a bond exists when ``min_distance < d < bond_scale * (r_i + r_j)``

with covalent radii sourced from :data:`confflow.core.data.GV_COVALENT_RADII`.
Callers keep their own ``bond_scale`` (confgen and refine use different
defaults), but the radii source, the minimum-distance safeguard, and the
unknown-element policy are centralised here so the same atoms/coordinates/scale
always produce the same topology.

Unknown elements (missing or zero radius) fail closed with
:class:`UnknownElementError`; callers decide whether that is a hard error or an
"invalid/unresolved" classification, but they must not silently invent a radius.

SciPy/NumPy are imported lazily so importing ``confflow.core`` stays cheap.
"""

from __future__ import annotations

from collections.abc import Sequence

from .data import GV_COVALENT_RADII

__all__ = [
    "MIN_BOND_DISTANCE_ANGSTROM",
    "UnknownElementError",
    "covalent_radius",
    "has_known_covalent_radii",
    "infer_bond_pairs",
    "build_adjacency",
]

#: Atoms closer than this are never bonded (guards against duplicate/near-zero
#: coordinate artifacts).
MIN_BOND_DISTANCE_ANGSTROM = 0.4


class UnknownElementError(ValueError):
    """An atom has no usable covalent radius."""


def covalent_radius(atomic_number: int) -> float | None:
    """Return the covalent radius for an atomic number, or ``None`` if unknown."""
    if 0 <= atomic_number < len(GV_COVALENT_RADII):
        radius = float(GV_COVALENT_RADII[atomic_number])
        if radius > 0.0:
            return radius
    return None


def has_known_covalent_radii(atomic_numbers: Sequence[int]) -> bool:
    """Return True when every atom has a usable covalent radius."""
    return all(covalent_radius(int(z)) is not None for z in atomic_numbers)


def infer_bond_pairs(
    atomic_numbers: Sequence[int],
    coords,
    *,
    bond_scale: float,
    min_distance: float = MIN_BOND_DISTANCE_ANGSTROM,
) -> list[tuple[int, int]]:
    """Return sorted ``(i, j)`` bonded pairs for the given atoms and coordinates."""
    import numpy as np  # local import: keep ``confflow.core`` import-light

    numbers = [int(z) for z in atomic_numbers]
    arr = np.asarray(coords, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 3 or arr.shape[0] != len(numbers):
        raise ValueError("coordinate shape does not match atoms")
    if not np.all(np.isfinite(arr)):
        raise ValueError("coordinates contain NaN or infinity")

    # Validate atoms before the small-system early return so a single unknown
    # atom (or non-finite coordinate) still fails closed.
    radii_list = [covalent_radius(z) for z in numbers]
    if any(radius is None for radius in radii_list):
        raise UnknownElementError("atom without a covalent radius")
    if len(numbers) <= 1:
        return []

    radii = np.array([float(radius) for radius in radii_list], dtype=np.float64)

    from scipy.spatial import cKDTree

    scale = float(bond_scale)
    lower_sq = float(min_distance) * float(min_distance)
    tree = cKDTree(arr)
    candidate_pairs = tree.query_pairs(float(radii.max() * 2.0 * scale), output_type="ndarray")

    pairs: list[tuple[int, int]] = []
    for i, j in candidate_pairs:
        i = int(i)
        j = int(j)
        threshold = (radii[i] + radii[j]) * scale
        diff = arr[i] - arr[j]
        distance_sq = float(diff @ diff)
        if lower_sq < distance_sq < threshold * threshold:
            pairs.append((i, j))
    return sorted(pairs)


def build_adjacency(
    atomic_numbers: Sequence[int],
    coords,
    *,
    bond_scale: float,
    min_distance: float = MIN_BOND_DISTANCE_ANGSTROM,
) -> list[list[int]]:
    """Return a sorted neighbour list per atom using :func:`infer_bond_pairs`."""
    numbers = [int(z) for z in atomic_numbers]
    adjacency: list[list[int]] = [[] for _ in numbers]
    for i, j in infer_bond_pairs(numbers, coords, bond_scale=bond_scale, min_distance=min_distance):
        adjacency[i].append(j)
        adjacency[j].append(i)
    return [sorted(row) for row in adjacency]
