#!/usr/bin/env python3

"""Chain-mode torsion science for V4 confgen (pure numeric).

Extracted from the pure-numpy subset of ``confflow.blocks.confgen`` —
``rotations`` (chain language, angle resolution, Rodrigues rotation,
BFS side selection, ring refusal) and ``collision`` (clash rule) — with
no import of the legacy generator, pools, RDKit molecules, MMFF, XYZ
I/O, or CLI.  Dependencies: stdlib + NumPy only.

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
    "parse_bond_pair",
    "parse_chain",
    "resolve_angle_lists",
    "rotate_atoms_around_bond",
    "rotating_side",
    "topological_distance_matrix",
]

#: Topological-hop cutoff for clash filtering, mirroring
#: ``collision.check_clash_core`` (``ignore_hops = 3``): pairs within
#: three bonds are ignored (non-bonded interaction standard).
TOPO_IGNORE_HOPS = 3


def parse_chain(chain: str) -> list[int]:
    """Parse a 1-based dash-separated chain to 0-based indices.

    Source: ``rotations._parse_chain``.  Raises :class:`ValueError` on
    malformed, non-positive, or duplicated entries.
    """
    parts = [item.strip() for item in str(chain).replace(",", "-").split("-") if item.strip()]
    if len(parts) < 2:
        raise ValueError(f"chain format error: {chain!r}")
    try:
        atoms_1based = [int(item) for item in parts]
    except ValueError as exc:
        raise ValueError(f"chain must be a list of integers: {chain!r}") from exc
    if any(item <= 0 for item in atoms_1based):
        raise ValueError(f"chain indices must be positive (1-based): {chain!r}")
    atoms = [item - 1 for item in atoms_1based]
    if len(set(atoms)) != len(atoms):
        raise ValueError(f"chain contains duplicate atoms: {chain!r}")
    return atoms


def parse_bond_pair(text: str) -> tuple[int, int]:
    """Parse one ``"a-b"`` 1-based bond pair to a 0-based tuple."""
    parts = [item.strip() for item in str(text).replace(",", "-").split("-") if item.strip()]
    if len(parts) != 2:
        raise ValueError(f"bond entries must be 'a-b' pairs, got {text!r}")
    try:
        first, second = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError(f"bond entries must be 'a-b' pairs, got {text!r}") from exc
    if first <= 0 or second <= 0 or first == second:
        raise ValueError(f"bond entries must name two distinct atoms, got {text!r}")
    return first - 1, second - 1


def resolve_angle_lists(
    n_bonds: int,
    chain_steps: str | None,
    chain_angles: str | None,
    angle_step: int,
) -> list[list[float]]:
    """Resolve per-bond rotation angle lists.

    Source: ``rotations._resolve_angle_lists`` (legacy ``--steps`` /
    ``--angles`` spellings).  Raises :class:`ValueError` on count or
    range mismatches.
    """
    if chain_angles is not None:
        segs = [item.strip() for item in str(chain_angles).split(";") if item.strip()]
        if len(segs) != n_bonds:
            raise ValueError(
                f"chain_angles needs {n_bonds} ';'-separated segments, got {len(segs)}"
            )
        out: list[list[float]] = []
        for seg in segs:
            vals = [item.strip() for item in seg.split(",") if item.strip()]
            if not vals:
                raise ValueError("chain_angles segment is empty")
            try:
                out.append([float(item) for item in vals])
            except ValueError as exc:
                raise ValueError(f"chain_angles must be numbers: {seg!r}") from exc
        return out
    if chain_steps is not None:
        parts = [item.strip() for item in str(chain_steps).split(",") if item.strip()]
        if len(parts) != n_bonds:
            raise ValueError(f"chain_steps needs {n_bonds} values, got {len(parts)}")
        try:
            steps = [int(item) for item in parts]
        except ValueError as exc:
            raise ValueError(f"chain_steps must be integers: {chain_steps!r}") from exc
        if any(item <= 0 or item > 360 for item in steps):
            raise ValueError(f"chain_steps must be in 1..360: {chain_steps!r}")
        return [[float(angle) for angle in range(0, 360, item)] for item in steps]
    return [[float(angle) for angle in range(0, 360, angle_step)] for _ in range(n_bonds)]


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
        and dist_near[index] <= dist_far[index]
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
