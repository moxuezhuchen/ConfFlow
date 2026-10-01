#!/usr/bin/env python3

"""ConfGen v3 ring-lane pure geometry utilities.

Numeric helpers for isolated covalent 4/5/6-membered rings: periodic angle
conventions, endocyclic torsions, proper-rotation Kabsch alignment, local
substituent frames, substituent partitioning with multi-anchor detection, and
a self-contained non-bonded clash audit.

Dependencies: stdlib + NumPy only. No ``confflow`` imports (ring science is
independent of legacy runners, orchestration, and energy optimization).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

__all__ = [
    "COVALENT_RADII",
    "DegenerateFrameError",
    "MultiAnchorError",
    "Substituent",
    "TOPO_IGNORE_HOPS",
    "circular_distance_deg",
    "circular_rms_deg",
    "clash_pairs",
    "dihedral_deg",
    "kabsch_proper",
    "local_frame",
    "partition_substituents",
    "rigid_rmsd",
    "ring_torsions",
    "rotation_between_frames",
    "topological_distances",
    "wrap_deg",
]

#: Non-bonded clash rule mirrors the legacy confgen convention (pairs within
#: this many topological hops are ignored); reimplemented here so ring science
#: stays independent of legacy runner imports.
TOPO_IGNORE_HOPS = 3


class DegenerateFrameError(ValueError):
    """Raised when a local frame is singular (collinear/degenerate)."""


class MultiAnchorError(ValueError):
    """Raised when a substituent fragment touches two or more ring atoms."""


@dataclass(frozen=True, slots=True)
class Substituent:
    """One non-ring fragment attached to a single ring anchor atom."""

    anchor: int
    root: int
    members: tuple[int, ...]


def wrap_deg(angle: float) -> float:
    """Wrap an angle in degrees to the periodic interval (-180, 180]."""
    wrapped = float(angle) % 360.0
    if wrapped > 180.0:
        wrapped -= 360.0
    if wrapped <= -180.0:
        wrapped += 360.0
    return wrapped


def circular_distance_deg(first: float, second: float) -> float:
    """Return the absolute periodic distance between two angles in degrees."""
    return abs(wrap_deg(float(first) - float(second)))


def circular_rms_deg(first: np.ndarray, second: np.ndarray) -> float:
    """Return the periodic RMS deviation between two angle vectors."""
    diffs = np.asarray(first, dtype=float) - np.asarray(second, dtype=float)
    wrapped = (diffs + 180.0) % 360.0 - 180.0
    return float(np.sqrt(np.mean(wrapped**2)))


def dihedral_deg(p0: np.ndarray, p1: np.ndarray, p2: np.ndarray, p3: np.ndarray) -> float:
    """Return the dihedral angle p0-p1-p2-p3 in degrees, in (-180, 180].

    Standard atan2 convention: angle between the (p0,p1,p2) and (p1,p2,p3)
    half-planes, sign from the right-hand rule about the p1->p2 axis.
    """
    b0 = np.asarray(p0, dtype=float) - np.asarray(p1, dtype=float)
    b1 = np.asarray(p2, dtype=float) - np.asarray(p1, dtype=float)
    b2 = np.asarray(p3, dtype=float) - np.asarray(p2, dtype=float)
    b1_norm = float(np.linalg.norm(b1))
    if b1_norm < 1e-12:
        raise DegenerateFrameError("dihedral central bond is degenerate")
    b1_unit = b1 / b1_norm
    v = b0 - float(np.dot(b0, b1_unit)) * b1_unit
    w = b2 - float(np.dot(b2, b1_unit)) * b1_unit
    if float(np.linalg.norm(v)) < 1e-12 or float(np.linalg.norm(w)) < 1e-12:
        raise DegenerateFrameError("dihedral outer bond is collinear with axis")
    x = float(np.dot(v, w))
    y = float(np.dot(np.cross(b1_unit, v), w))
    return wrap_deg(float(np.degrees(np.arctan2(y, x))))


def ring_torsions(ring_coords: np.ndarray) -> np.ndarray:
    """Return endocyclic torsions tau_k = dihedral(k,k+1,k+2,k+3 mod n).

    Parameters
    ----------
    ring_coords:
        Ordered ``(n, 3)`` ring atom positions; index 0 is the traversal
        anchor and order is the traversal direction.
    """
    coords = np.asarray(ring_coords, dtype=float)
    if coords.ndim != 2 or coords.shape[1] != 3 or coords.shape[0] < 4:
        raise ValueError(f"ring_coords must be (n,3) with n>=4, got {coords.shape}")
    n_atoms = coords.shape[0]
    return np.array(
        [
            dihedral_deg(
                coords[index % n_atoms],
                coords[(index + 1) % n_atoms],
                coords[(index + 2) % n_atoms],
                coords[(index + 3) % n_atoms],
            )
            for index in range(n_atoms)
        ]
    )


def kabsch_proper(mobile: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return proper rotation ``R`` and translation ``t`` mapping mobile->target.

    Kabsch least-squares fit with determinant correction, so ``R`` is always
    a proper rotation (det +1): mirror-image templates stay distinct states
    instead of being silently superimposed.
    """
    p = np.asarray(mobile, dtype=float)
    q = np.asarray(target, dtype=float)
    if p.shape != q.shape or p.ndim != 2 or p.shape[1] != 3:
        raise ValueError("kabsch inputs must be matching (n,3) arrays")
    centroid_p = p.mean(axis=0)
    centroid_q = q.mean(axis=0)
    covariance = (p - centroid_p).T @ (q - centroid_q)
    u, _, vt = np.linalg.svd(covariance)
    sign = np.sign(np.linalg.det(u @ vt))
    if sign == 0.0:
        raise DegenerateFrameError("kabsch covariance is rank-deficient")
    correction = np.diag([1.0, 1.0, sign])
    rotation = u @ correction @ vt
    translation = centroid_q - rotation @ centroid_p
    return rotation, translation


def rigid_rmsd(mobile: np.ndarray, target: np.ndarray) -> float:
    """Return the best-fit proper-rotation RMSD between two point sets."""
    rotation, translation = kabsch_proper(mobile, target)
    fitted = np.asarray(mobile, dtype=float) @ rotation.T + translation
    return float(np.sqrt(np.mean(np.sum((fitted - target) ** 2, axis=1))))


def local_frame(prev: np.ndarray, center: np.ndarray, nxt: np.ndarray) -> np.ndarray:
    """Return the 3x3 local frame at a ring atom (rows e1, e2, e3).

    e1 points from center to prev, e2 is the center->next component
    orthogonal to e1, e3 = e1 x e2 (right-handed). Raises
    :class:`DegenerateFrameError` when prev/center/next are collinear.
    """
    first = np.asarray(prev, dtype=float) - np.asarray(center, dtype=float)
    second = np.asarray(nxt, dtype=float) - np.asarray(center, dtype=float)
    first_norm = float(np.linalg.norm(first))
    if first_norm < 1e-12:
        raise DegenerateFrameError("local frame has a zero-length bond")
    e1 = first / first_norm
    second_ortho = second - float(np.dot(second, e1)) * e1
    second_norm = float(np.linalg.norm(second_ortho))
    if second_norm < 1e-8:
        raise DegenerateFrameError("local frame is collinear (singular)")
    e2 = second_ortho / second_norm
    e3 = np.cross(e1, e2)
    frame = np.stack([e1, e2, e3])
    if abs(float(np.linalg.det(frame)) - 1.0) > 1e-6:
        raise DegenerateFrameError("local frame is not a proper rotation frame")
    return frame


def rotation_between_frames(old: np.ndarray, new: np.ndarray) -> np.ndarray:
    """Return the proper rotation mapping vectors from the old frame to new."""
    old_m = np.asarray(old, dtype=float)
    new_m = np.asarray(new, dtype=float)
    rotation = new_m.T @ old_m
    if abs(float(np.linalg.det(rotation)) - 1.0) > 1e-6:
        raise DegenerateFrameError("frame rotation is not a proper rotation")
    return rotation


def topological_distances(
    adjacency: list[list[int]] | tuple[tuple[int, ...], ...],
) -> list[list[int]]:
    """Return the all-pairs topological distance matrix of the bond graph."""
    n_atoms = len(adjacency)
    inf = 10**9
    matrix: list[list[int]] = []
    for source in range(n_atoms):
        dist = [inf] * n_atoms
        dist[source] = 0
        queue: deque[int] = deque([source])
        while queue:
            current = queue.popleft()
            for nxt in adjacency[current]:
                if dist[nxt] <= dist[current] + 1:
                    continue
                dist[nxt] = dist[current] + 1
                queue.append(nxt)
        matrix.append(dist)
    return matrix


#: Covalent radii (Angstrom) for the ring clash audit. Compact H/C/N/O/halogen
#: table; unknown elements fall back to 1.5 with an audit flag, never silently.
COVALENT_RADII: dict[str, float] = {
    "H": 0.31,
    "C": 0.76,
    "N": 0.71,
    "O": 0.66,
    "F": 0.57,
    "P": 1.07,
    "S": 1.05,
    "CL": 1.02,
    "BR": 1.20,
    "I": 1.39,
    "SI": 1.11,
    "B": 0.84,
}


def clash_pairs(
    coords: np.ndarray,
    elements: list[str],
    topo: list[list[int]],
    threshold: float = 0.65,
) -> tuple[list[tuple[int, int, float, float]], bool]:
    """Audit non-bonded clashes; return (clash list, used_fallback_radii flag).

    A pair clashes when ``dist < (R_i + R_j) * threshold``; pairs within
    ``TOPO_IGNORE_HOPS`` topological hops are ignored. Each clash entry is
    ``(i, j, distance, limit)`` sorted by severity.
    """
    positions = np.asarray(coords, dtype=float)
    n_atoms = positions.shape[0]
    radii: list[float] = []
    fallback = False
    for symbol in elements:
        radius = COVALENT_RADII.get(symbol.upper())
        if radius is None:
            radius = 1.5
            fallback = True
        radii.append(radius)
    clashes: list[tuple[int, int, float, float]] = []
    for first in range(n_atoms):
        for second in range(first + 1, n_atoms):
            if topo[first][second] <= TOPO_IGNORE_HOPS:
                continue
            delta = positions[first] - positions[second]
            dist = float(np.linalg.norm(delta))
            limit = (radii[first] + radii[second]) * float(threshold)
            if dist < limit:
                clashes.append((first, second, dist, limit))
    clashes.sort(key=lambda item: item[2] / item[3])
    return clashes, fallback


def partition_substituents(
    n_atoms: int,
    adjacency: list[list[int]] | tuple[tuple[int, ...], ...],
    ring_set: frozenset[int],
    blocked: frozenset[int] | None = None,
) -> tuple[list[Substituent], list[dict[str, int]]]:
    """Partition non-ring atoms into single-anchor substituent fragments.

    The walk never enters *blocked* atoms (default: *ring_set*; pass the union
    of all ring systems so linked rings are never absorbed as substituents).
    Returns ``(substituents, multi_anchor)`` where multi-anchor entries are
    ``{"atom": root, "anchors": count}`` evidence dicts for fragments bonded
    (transitively) to two or more blocked atoms. Multi-anchor fragments are
    reported, never silently split or rotated piece-wise.
    """
    blocked_set = frozenset(ring_set) if blocked is None else frozenset(blocked)
    assigned = set(blocked_set)
    substituents: list[Substituent] = []
    multi_anchor: list[dict[str, int]] = []
    for anchor in sorted(ring_set):
        for root in sorted(adjacency[anchor]):
            if root in assigned:
                continue
            members: list[int] = []
            touched = {anchor}
            queue: deque[int] = deque([root])
            assigned.add(root)
            while queue:
                current = queue.popleft()
                members.append(current)
                for nxt in adjacency[current]:
                    if nxt in blocked_set:
                        touched.add(nxt)
                        continue
                    if nxt in assigned:
                        continue
                    assigned.add(nxt)
                    queue.append(nxt)
            if len(touched) > 1:
                multi_anchor.append({"atom": root, "anchors": len(touched)})
                continue
            substituents.append(
                Substituent(anchor=anchor, root=root, members=tuple(sorted(members)))
            )
    substituents.sort(key=lambda item: (item.anchor, item.root))
    return substituents, multi_anchor
