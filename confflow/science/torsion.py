#!/usr/bin/env python3

"""Torsion mechanics for V4 confgen (pure numeric).

Extracted from the pure-numpy subset of ``confflow.blocks.confgen`` —
``rotations`` (Rodrigues rotation, BFS side selection, ring refusal) and
``collision`` (clash rule) — with no import of the legacy generator,
pools, RDKit molecules, MMFF, XYZ I/O, or CLI.  Dependencies: stdlib +
NumPy only.

Each public helper names its source algorithm.  Callers supply atomic
numbers, coordinates, radii, and adjacency explicitly; nothing is
inferred from files or global state.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Sequence

import numpy as np

__all__ = [
    "TOPO_IGNORE_HOPS",
    "bfs_distances",
    "clashes",
    "edge_in_cycle",
    "rotate_atoms_around_bond",
    "rotating_side",
    "topological_distance_matrix",
]

#: Topological-hop cutoff for clash filtering, mirroring
#: ``collision.check_clash_core`` (``ignore_hops = 3``): pairs within
#: three bonds are ignored (non-bonded interaction standard).
TOPO_IGNORE_HOPS = 3


def bfs_distances(adjacency: Sequence[Sequence[int]], sources: Sequence[int]) -> list[int]:
    """Multi-source shortest-path distances on the bond graph.

    Source: ``rotations._bfs_distances_multi``.
    """
    n_atoms = len(adjacency)
    inf = 10**9
    dist = [inf] * n_atoms
    queue: deque[int] = deque()
    for item in sources:
        if 0 <= item < n_atoms and dist[item] != 0:
            dist[item] = 0
            queue.append(item)
    while queue:
        current = queue.popleft()
        step = dist[current] + 1
        for nxt in adjacency[current]:
            if dist[nxt] <= step:
                continue
            dist[nxt] = step
            queue.append(nxt)
    return dist


def edge_in_cycle(adjacency: Sequence[Sequence[int]], first: int, second: int) -> bool:
    """Return whether the ``first-second`` bond lies on a ring.

    Source: ``rotations._edge_in_cycle``.
    """
    if first == second:
        return False
    visited = {first}
    stack = [first]
    while stack:
        current = stack.pop()
        for nxt in adjacency[current]:
            if (current == first and nxt == second) or (current == second and nxt == first):
                continue
            if nxt == second:
                return True
            if nxt in visited:
                continue
            visited.add(nxt)
            stack.append(nxt)
    return False


def rotating_side(
    adjacency: Sequence[Sequence[int]],
    n_atoms: int,
    left: int,
    right: int,
    near_sources: Sequence[int],
    far_sources: Sequence[int],
) -> list[int]:
    """Return the 0-based indices rotating with a chain bond.

    Source: the side-selection half of ``rotations._build_chain_rotations``:
    atoms strictly closer (topologically) to the near side than the far
    side rotate; the bond atoms themselves and every far-side chain atom
    stay fixed.
    """
    dist_near = bfs_distances(adjacency, near_sources)
    dist_far = bfs_distances(adjacency, far_sources)
    far_set = set(far_sources)
    return [
        index
        for index in range(n_atoms)
        if index != left
        and index != right
        and index not in far_set
        and dist_near[index] < dist_far[index]
    ]


def rotate_atoms_around_bond(
    coords: np.ndarray,
    pivot: int,
    second: int,
    atom_indices: Sequence[int],
    angle_deg: float,
) -> None:
    """Rotate atoms about the pivot-second bond axis (Rodrigues, in place).

    Source: ``rotations._rotate_atoms_around_bond``.  The bond atoms stay
    fixed; a degenerate axis is a no-op.
    """
    if len(atom_indices) == 0:
        return
    axis = coords[second] - coords[pivot]
    norm = float(np.linalg.norm(axis))
    if norm < 1e-12:
        return
    unit = axis / norm
    theta = float(angle_deg) * math.pi / 180.0
    cosine, sine = math.cos(theta), math.sin(theta)
    selected = np.asarray(list(atom_indices), dtype=np.intp)
    vectors = coords[selected] - coords[pivot]
    ux, uy, uz = (float(unit[0]), float(unit[1]), float(unit[2]))
    cross = np.column_stack(
        [
            uy * vectors[:, 2] - uz * vectors[:, 1],
            uz * vectors[:, 0] - ux * vectors[:, 2],
            ux * vectors[:, 1] - uy * vectors[:, 0],
        ]
    )
    dot = (vectors[:, 0] * ux + vectors[:, 1] * uy + vectors[:, 2] * uz).reshape(-1, 1)
    coords[selected] = (
        coords[pivot]
        + vectors * cosine
        + cross * sine
        + (unit.reshape(1, 3) * dot) * (1.0 - cosine)
    )


def topological_distance_matrix(adjacency: Sequence[Sequence[int]]) -> list[list[int]]:
    """Return the all-pairs topological distance matrix of the bond graph."""
    return [bfs_distances(adjacency, [index]) for index in range(len(adjacency))]


def clashes(
    coords: np.ndarray,
    radii: Sequence[float],
    topo: Sequence[Sequence[int]],
    threshold: float,
) -> bool:
    """Return whether any non-bonded pair clashes.

    Source: ``collision.check_clash_core``: pairs within
    ``TOPO_IGNORE_HOPS`` topological hops are ignored, otherwise
    ``dist < (R_i + R_j) * threshold`` is a clash.
    """
    n_atoms = int(coords.shape[0])
    for first in range(n_atoms):
        for second in range(first + 1, n_atoms):
            if topo[first][second] <= TOPO_IGNORE_HOPS:
                continue
            delta = coords[first] - coords[second]
            dist_sq = float(delta @ delta)
            limit = (float(radii[first]) + float(radii[second])) * float(threshold)
            if dist_sq < limit * limit:
                return True
    return False
