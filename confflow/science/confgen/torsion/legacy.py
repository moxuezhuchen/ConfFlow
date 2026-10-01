#!/usr/bin/env python3

"""Legacy torsion grid adapter (CORE lane, explicitly versioned).

Reproduces the legacy executor's full-grid geometry and native ordinals
bit-for-bit (chain-position-aware side selection, cumulative application in
declared order, ``itertools.product`` row-major ordinals, add/del_bond
topology corrections, post-geometry clash filtering) WITHOUT importing the
legacy runner. The legacy sign convention rotates the selected fragment
about the chain-direction axis (pivot=left, second=right); the v3
:class:`TorsionStage` instead normalizes commanded==measured, so on acyclic
systems stage coordinates equal legacy coordinates at negated angles (pinned
by test).

``legacy_cap_v1`` preserves the old survivor-based cap: shuffle the
post-geometry survivors with ``random.Random(sha256(seed:logical_key))``,
truncate, re-sort by ordinal. This is DIFFERENT science from v3
pre-geometry sampling (which caps attempted targets before geometry and
uses Floyd sampling): never substitute one for the other silently.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Iterator, Sequence
from typing import Any

import numpy as np

from confflow.science.torsion import (
    clashes,
    edge_in_cycle,
    rotate_atoms_around_bond,
    rotating_side,
)

__all__ = [
    "legacy_cap_v1",
    "legacy_grid_geometries",
    "legacy_rotatable_bonds",
    "iter_legacy_grid",
]


def iter_legacy_grid(
    angle_lists: Sequence[Sequence[float]],
) -> Iterator[tuple[int, tuple[float, ...]]]:
    """Yield ``(ordinal, combo)`` in legacy product order (lazy, no geometry).

    Versioned science home for the executor's legacy-chains enumeration:
    row-major ``itertools.product`` order (last bond fastest), so ordinals
    equal the legacy grid ordinals. Geometry application stays in
    :func:`legacy_grid_geometries`; survivor capping stays in
    :func:`legacy_cap_v1` (different science from v3 pre-geometry
    sampling). No behavior change, only relocation per the integration
    interface request.
    """
    import itertools

    lists = [list(map(float, angles)) for angles in angle_lists]
    for ordinal, combo in enumerate(itertools.product(*lists)):
        yield ordinal, tuple(float(angle) for angle in combo)


def legacy_rotatable_bonds(
    chains: Sequence[Sequence[int]],
    adjacency: Sequence[Sequence[int]],
    rotate_side: str = "left",
) -> list[tuple[int, int, list[int]]]:
    """Build legacy (left, right, rotating) bonds with chain-aware sides.

    Mirrors the executor exactly: for each chain bond at its chain position,
    near/far sources are the chain atoms on each side. Ring bonds fail
    closed (exclude via preserve/no_rotate upstream).
    """
    if rotate_side not in ("left", "right"):
        raise ValueError("rotate_side must be 'left' or 'right'")
    n_atoms = len(adjacency)
    rot_bonds: list[tuple[int, int, list[int]]] = []
    for chain in chains:
        for position, (left, right) in enumerate(zip(chain, chain[1:])):
            if right not in adjacency[left]:
                raise ValueError(f"chain atoms {left + 1}-{right + 1} are not bonded")
            if edge_in_cycle(adjacency, left, right):
                raise ValueError(
                    f"chain bond {left + 1}-{right + 1} is a ring bond and "
                    "cannot be rotated independently"
                )
            if rotate_side == "left":
                near, far = list(chain[: position + 1]), list(chain[position + 1 :])
            else:
                near, far = list(chain[position + 1 :]), list(chain[: position + 1])
            rotating = rotating_side(adjacency, n_atoms, left, right, near, far)
            rot_bonds.append((left, right, rotating))
    return rot_bonds


def legacy_grid_geometries(
    base_coords: np.ndarray | Sequence[Sequence[float]],
    chains: Sequence[Sequence[int]],
    angle_lists: Sequence[Sequence[float]],
    adjacency: Sequence[Sequence[int]],
    *,
    rotate_side: str = "left",
    radii: Sequence[float] | None = None,
    topo: Sequence[Sequence[int]] | None = None,
    clash_threshold: float | None = None,
) -> Iterator[tuple[int, np.ndarray]]:
    """Yield ``(native ordinal, coordinates)`` over the legacy full grid.

    Product row-major order (last bond fastest) with cumulative application
    in declared order -- identical ordinals and coordinates to the legacy
    executor. When radii/topo/threshold are given, clashing points are
    dropped exactly like the legacy clash filter.
    """
    import itertools

    rot_bonds = legacy_rotatable_bonds(chains, adjacency, rotate_side)
    lists = [list(map(float, angles)) for angles in angle_lists]
    if len(lists) != len(rot_bonds):
        raise ValueError("angle_lists must hold one list per chain bond")
    base = np.asarray(base_coords, dtype=np.float64)
    for ordinal, combo in enumerate(itertools.product(*lists)):
        coords = base.copy()
        for (left, right, rotating), angle in zip(rot_bonds, combo):
            rotate_atoms_around_bond(coords, left, right, rotating, float(angle))
        if radii is not None and topo is not None and clash_threshold is not None:
            if clashes(coords, list(radii), topo, float(clash_threshold)):
                continue
        yield ordinal, coords


def legacy_cap_v1(
    items: Sequence[Any],
    *,
    seed: int,
    logical_key: str,
    cap: int,
    key: Any = None,
) -> list[Any]:
    """Apply the explicitly versioned legacy survivor cap (v1).

    Shuffles post-geometry survivors with
    ``random.Random(sha256(f"{seed}:{logical_key}")[:8])``, truncates to
    *cap*, then re-sorts by *key* (native ordinals) when given -- exactly
    the legacy executor's shuffle-truncate-restore behavior. Documented NOT
    equivalent to v3 pre-geometry sampling: the cap counts kept survivors
    (post clash-filter), not attempted targets.
    """
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an explicit integer")
    if not isinstance(logical_key, str) or not logical_key:
        raise ValueError("logical_key must be a non-empty string")
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        raise ValueError("cap must be an integer >= 1")
    shuffler = random.Random(
        int.from_bytes(hashlib.sha256(f"{seed}:{logical_key}".encode()).digest()[:8], "big")
    )
    pool = list(items)
    shuffler.shuffle(pool)
    survivors = pool[:cap]
    if key is not None:
        survivors.sort(key=key)
    return survivors


def legacy_selection_fingerprint(total: int, *, seed: int, logical_key: str, cap: int) -> list[int]:
    """Return the sorted survivor ordinals a v1 cap would keep (audit helper)."""
    kept = legacy_cap_v1(list(range(total)), seed=seed, logical_key=logical_key, cap=cap)
    return sorted(kept)
