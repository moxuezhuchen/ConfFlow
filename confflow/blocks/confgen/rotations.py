#!/usr/bin/env python3

"""Chain rotation and graph algorithm module for ConfGen.

Contains chain definition parsing, graph topology algorithms,
and rotatable bond construction. Split from generator.py for readability.
"""

from __future__ import annotations

from collections import deque
from typing import Any

import numpy as np

__all__: list[str] = []

# ------------------------------------------------------------------------------
# Chain parsing
# ------------------------------------------------------------------------------


def _parse_chain(chain_str: str) -> list[int]:
    """Parse a chain string, e.g. '81-69-78-86-92' -> [80, 68, 77, 85, 91] (0-based)."""
    parts = [p.strip() for p in chain_str.replace(",", "-").split("-") if p.strip()]
    if len(parts) < 2:
        raise ValueError(f"chain format error: {chain_str}")
    try:
        atoms_1based = [int(x) for x in parts]
    except ValueError as e:
        raise ValueError(f"chain must be a list of integers: {chain_str}") from e
    if any(x <= 0 for x in atoms_1based):
        raise ValueError(f"chain indices must be positive (1-based): {chain_str}")
    atoms = [x - 1 for x in atoms_1based]
    if len(set(atoms)) != len(atoms):
        raise ValueError(f"chain contains duplicate atoms: {chain_str}")
    return atoms


def _parse_steps(steps_str: str, n_bonds: int) -> list[int]:
    parts = [p.strip() for p in steps_str.split(",") if p.strip()]
    if len(parts) != n_bonds:
        raise ValueError(
            f"steps requires {n_bonds} values (for {n_bonds} bonds), got {len(parts)}: {steps_str}"
        )
    steps = [int(x) for x in parts]
    if any(s <= 0 or s > 360 for s in steps):
        raise ValueError(f"steps must be in 1..360: {steps_str}")
    return steps


def _parse_angles(angles_str: str, n_bonds: int) -> list[list[float]]:
    """Parse angle lists for each bond, e.g. '0,120,240;0,60,120,180;180;0,120'."""
    segs = [s.strip() for s in angles_str.split(";") if s.strip()]
    if len(segs) != n_bonds:
        raise ValueError(
            f"angles requires {n_bonds} segments (';' separated), got {len(segs)}: {angles_str}"
        )
    out: list[list[float]] = []
    for seg in segs:
        vals = [v.strip() for v in seg.split(",") if v.strip()]
        if not vals:
            raise ValueError(f"angles segment cannot be empty: {angles_str}")
        out.append([float(v) for v in vals])
    return out


# ------------------------------------------------------------------------------
# Graph topology algorithms
# ------------------------------------------------------------------------------


def _build_adjacency(mol: Any) -> list[set]:
    n_atoms = mol.GetNumAtoms()
    adjacency: list[set] = [set() for _ in range(n_atoms)]
    for b in mol.GetBonds():
        i = b.GetBeginAtomIdx()
        j = b.GetEndAtomIdx()
        adjacency[i].add(j)
        adjacency[j].add(i)
    return adjacency


def _bfs_distances(adjacency: list[set], source: int) -> list[int]:
    """Compute shortest-path distances from source to all nodes on an unweighted graph."""
    n = len(adjacency)
    INF = 10**9
    dist = [INF] * n
    dist[source] = 0
    q: deque[int] = deque([source])
    while q:
        cur = q.popleft()
        nd = dist[cur] + 1
        for nxt in adjacency[cur]:
            if dist[nxt] != INF:
                continue
            dist[nxt] = nd
            q.append(nxt)
    return dist


def _bfs_distances_multi(adjacency: list[set], sources: list[int]) -> list[int]:
    """Multi-source shortest path: dist[x] = min over all sources s of dist(s, x)."""
    n = len(adjacency)
    INF = 10**9
    dist = [INF] * n
    q: deque[int] = deque()
    for s in sources:
        if 0 <= s < n and dist[s] != 0:
            dist[s] = 0
            q.append(s)
    while q:
        cur = q.popleft()
        nd = dist[cur] + 1
        for nxt in adjacency[cur]:
            if dist[nxt] <= nd:
                continue
            dist[nxt] = nd
            q.append(nxt)
    return dist


def _component_nodes(adjacency: list[set], start: int, blocked: int) -> set:
    visited = set([start])
    stack = [start]
    while stack:
        cur = stack.pop()
        for nxt in adjacency[cur]:
            if (cur == start and nxt == blocked) or (cur == blocked and nxt == start):
                continue
            if nxt in visited:
                continue
            visited.add(nxt)
            stack.append(nxt)
    return visited


def _edge_in_cycle(adjacency: list[set], u: int, v: int) -> bool:
    """Return True if edge u-v lies on a cycle (u remains reachable from v after removal)."""
    if u == v:
        return False
    visited = set([u])
    stack = [u]
    while stack:
        cur = stack.pop()
        for nxt in adjacency[cur]:
            if (cur == u and nxt == v) or (cur == v and nxt == u):
                continue
            if nxt == v:
                return True
            if nxt in visited:
                continue
            visited.add(nxt)
            stack.append(nxt)
    return False


def _validate_chain_bonds(mol: Any, parsed_chains: list[list[int]], filename: str) -> None:
    """Validate that adjacent atoms in each chain are bonded."""
    missing = []
    for ch in parsed_chains:
        for i in range(len(ch) - 1):
            a = int(ch[i])
            b = int(ch[i + 1])
            if mol.GetBondBetweenAtoms(a, b) is None:
                missing.append((a, b))
    if missing:
        pairs = ", ".join([f"{a + 1}-{b + 1}" for a, b in missing[:5]])
        extra = "" if len(missing) <= 5 else f" ... {len(missing)} total"
        raise ValueError(
            f"adjacent chain atoms not bonded: {pairs}{extra} (file: {filename}). "
            "Use --add_bond or adjust bond_threshold."
        )


# ------------------------------------------------------------------------------
# Rotatable bond construction
# ------------------------------------------------------------------------------


def _rotate_atoms_around_bond(
    coords: np.ndarray, i: int, j: int, atom_indices: np.ndarray, angle_deg: float
) -> None:
    """Rotate a set of atoms around the i-j bond axis (i and j stay fixed).

    Uses the Rodrigues rotation formula to modify *coords* in-place.

    Parameters
    ----------
    coords : ndarray
        Atomic coordinates, shape (N, 3).
    i : int
        Index of the first bond atom (pivot).
    j : int
        Index of the second bond atom.
    atom_indices : ndarray
        0-based indices of atoms to rotate.
    angle_deg : float
        Rotation angle in degrees.
    """
    if atom_indices.size == 0:
        return
    p1 = coords[i]
    p2 = coords[j]
    axis = p2 - p1
    norm = float(np.linalg.norm(axis))
    if norm < 1e-12:
        return
    u = axis / norm

    theta = float(angle_deg) * np.pi / 180.0
    c = float(np.cos(theta))
    s = float(np.sin(theta))

    v = coords[atom_indices] - p1
    ux, uy, uz = u
    # u x v
    cross = np.column_stack(
        [
            uy * v[:, 2] - uz * v[:, 1],
            uz * v[:, 0] - ux * v[:, 2],
            ux * v[:, 1] - uy * v[:, 0],
        ]
    )
    dot = (v[:, 0] * ux + v[:, 1] * uy + v[:, 2] * uz).reshape(-1, 1)
    v_rot = v * c + cross * s + (u.reshape(1, 3) * dot) * (1.0 - c)
    coords[atom_indices] = p1 + v_rot


def _resolve_angle_lists(
    parsed_chains: list[list[int]],
    chain_steps: list[str] | None,
    chain_angles: list[str] | None,
    angle_step: int,
) -> list[list[list[float]]]:
    """Resolve rotation angle lists for each bond in every chain."""
    per_chain: list[list[list[float]]] = []

    if chain_angles:
        if len(chain_angles) not in (1, len(parsed_chains)):
            raise ValueError(
                "--angles count must be 1 (applied to all chains) or match --chain count"
            )
        angles_specs = (
            chain_angles
            if len(chain_angles) == len(parsed_chains)
            else [chain_angles[0]] * len(parsed_chains)
        )
        for ch, ang in zip(parsed_chains, angles_specs):
            per_chain.append(_parse_angles(ang, len(ch) - 1))
    elif chain_steps:
        if len(chain_steps) not in (1, len(parsed_chains)):
            raise ValueError(
                "--steps count must be 1 (applied to all chains) or match --chain count"
            )
        step_specs = (
            chain_steps
            if len(chain_steps) == len(parsed_chains)
            else [chain_steps[0]] * len(parsed_chains)
        )
        for ch, st in zip(parsed_chains, step_specs):
            steps = _parse_steps(st, len(ch) - 1)
            per_chain.append([list(range(0, 360, int(s))) for s in steps])
    else:
        per_chain = [
            [list(range(0, 360, int(angle_step))) for _ in range(len(ch) - 1)]
            for ch in parsed_chains
        ]

    return per_chain


def _build_chain_rotations(
    mol: Any,
    parsed_chains: list[list[int]],
    per_chain_angle_lists: list[list[list[float]]],
    no_rotate: list[list[int]] | None,
    force_rotate: list[list[int]] | None,
    rotate_side: str,
) -> tuple[list[tuple[int, int, Any]], list[list[float]]]:
    """Build rotatable bonds and corresponding angle lists from chain definitions.

    Parameters
    ----------
    mol : Any
        RDKit molecule object.
    parsed_chains : list[list[int]]
        Parsed chain atom indices (0-based).
    per_chain_angle_lists : list[list[list[float]]]
        Angle lists for each bond in each chain.
    no_rotate : list[list[int]] or None
        Bond pairs to skip rotation.
    force_rotate : list[list[int]] or None
        Bond pairs to force rotation.
    rotate_side : str
        Which side to rotate ('left' or 'right').

    Returns
    -------
    tuple[list[tuple[int, int, Any]], list[list[float]]]
        (rot_bonds, angle_lists)
    """
    if rotate_side not in ("left", "right"):
        raise ValueError("rotate_side must be left or right")

    adjacency = _build_adjacency(mol)
    n_atoms = mol.GetNumAtoms()
    rot_bonds: list[tuple[int, int, Any]] = []
    angle_lists: list[list[float]] = []

    for ch, bond_angles in zip(parsed_chains, per_chain_angle_lists):
        for bi in range(len(ch) - 1):
            a_left = ch[bi]
            a_right = ch[bi + 1]

            if mol.GetBondBetweenAtoms(a_left, a_right) is None:
                raise ValueError(
                    f"no bond between adjacent chain atoms: {a_left + 1}-{a_right + 1} (use --add_bond or check chain indices)"
                )

            if no_rotate:
                pair = tuple(sorted((a_left, a_right)))
                if any(tuple(sorted((p[0] - 1, p[1] - 1))) == pair for p in no_rotate):
                    continue

            if rotate_side == "left":
                left_sources = ch[: bi + 1]
                right_sources = ch[bi + 1 :]
            else:
                left_sources = ch[bi + 1 :]
                right_sources = ch[: bi + 1]

            dist_left = _bfs_distances_multi(adjacency, left_sources)
            dist_right = _bfs_distances_multi(adjacency, right_sources)

            right_source_set = set(right_sources)
            rotate_atoms = [
                idx
                for idx in range(n_atoms)
                if idx not in (a_left, a_right)
                and idx not in right_source_set
                and dist_left[idx] <= dist_right[idx]
            ]

            if not rotate_atoms and force_rotate:
                pair = tuple(sorted((a_left, a_right)))
                if any(tuple(sorted((p[0] - 1, p[1] - 1))) == pair for p in force_rotate):
                    rotate_atoms = []

            rot_bonds.append((int(a_left), int(a_right), np.array(rotate_atoms, dtype=np.int64)))
            angle_lists.append([float(x) for x in bond_angles[bi]])

    return rot_bonds, angle_lists
