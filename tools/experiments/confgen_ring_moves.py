"""Experimental closed-ring moves; no production search defaults are changed.

A two-vertex graph cut identifies an arc plus its substituents. A proper rotation
of this component about the two fixed vertices preserves every graph-edge length.
Bond angles at the anchors can change: callers must apply the production stereo,
clash and post-optimisation topology checks. This is a proposal generator, not a
force field, a ring-state declaration or an equilibrium Monte Carlo sampler.

Pendant-ring inversion instead reflects a saturated ring backbone while carrying
each external branch through a proper local rotation. It preserves local bond
lengths, bond angles and tetrahedral hands, including at the stationary attachment.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class RingArc:
    """A movable component whose only neighbours outside it are two anchors."""

    anchors: tuple[int, int]
    moving: tuple[int, ...]
    ring: tuple[int, ...]


def ring_arcs(
    adjacency: Sequence[Mapping[int, object]],
    rings: Sequence[Sequence[int]],
    pinned: frozenset[int] = frozenset(),
    rigid_edges: frozenset[tuple[int, int]] = frozenset(),
) -> tuple[RingArc, ...]:
    """Find ring arcs disconnected by deleting two non-adjacent ring vertices.

    Components containing fixed atoms are excluded. Attached branches move in
    full, so the operation never silently stretches a bond to an omitted branch.
    Fused/bridged systems are accepted only when the actual graph has this cut.
    """
    found: dict[tuple[tuple[int, int], tuple[int, ...]], RingArc] = {}
    for ring_seq in rings:
        ring = tuple(ring_seq)
        if len(ring) < 5 or len(ring) > 9:
            continue
        if any(tuple(sorted((u, v))) in rigid_edges for u, v in zip(ring, ring[1:] + ring[:1])):
            continue
        ring_set = set(ring)
        for a, b in itertools.combinations(sorted(ring), 2):
            if b in adjacency[a]:
                continue
            anchors = {a, b}
            unseen = ring_set - anchors
            while unseen:
                component = {min(unseen)}
                frontier = list(component)
                while frontier:
                    u = frontier.pop()
                    for v in adjacency[u]:
                        if v not in anchors and v not in component:
                            component.add(v)
                            frontier.append(v)
                unseen -= component
                arc_atoms = component & ring_set
                if component & pinned or not 1 <= len(arc_atoms) < len(ring) - 2:
                    continue
                boundary = {v for u in component for v in adjacency[u] if v not in component}
                if boundary != anchors:
                    continue
                moving = tuple(sorted(component))
                key = ((a, b), moving)
                found.setdefault(key, RingArc((a, b), moving, ring))
    return tuple(found[key] for key in sorted(found))


def rotate_arc(coords: np.ndarray, arc: RingArc, degrees: float) -> np.ndarray:
    """Rotate an arc, returning a copy with all other coordinates unchanged."""
    xyz = np.asarray(coords, dtype=float)
    if xyz.ndim != 2 or xyz.shape[1] != 3 or not np.isfinite(xyz).all():
        raise ValueError("coords must be finite N by 3 coordinates")
    a, b = arc.anchors
    axis = xyz[b] - xyz[a]
    length = float(np.linalg.norm(axis))
    if length < 1e-10 or not np.isfinite(degrees):
        raise ValueError("rotation needs distinct anchors and a finite angle")
    axis /= length
    theta = np.radians(degrees)
    indices = list(arc.moving)
    delta = xyz[indices] - xyz[a]
    result = xyz.copy()
    result[indices] = (
        xyz[a]
        + np.cos(theta) * delta
        + np.sin(theta) * np.cross(axis, delta)
        + (1 - np.cos(theta)) * np.outer(delta @ axis, axis)
    )
    return result


@dataclass(frozen=True)
class PendantRing:
    """A simple ring with external branches attached at exactly one ring vertex each."""

    ring: tuple[int, ...]
    anchor: int
    branches: tuple[tuple[int, tuple[int, ...]], ...]


def pendant_rings(
    adjacency: Sequence[Mapping[int, object]],
    rings: Sequence[Sequence[int]],
    pinned: frozenset[int] = frozenset(),
    rigid_edges: frozenset[tuple[int, int]] = frozenset(),
) -> tuple[PendantRing, ...]:
    """Find saturated, unfused ring backbones with a single fixed attachment.

    Multiple attachments to a fixed core, chords and external bridges are
    rejected rather than approximated. In an unpinned molecule the largest
    external branch defines the stationary attachment.
    """
    result = []
    for ring_seq in rings:
        ring = tuple(ring_seq)
        members = set(ring)
        if not 5 <= len(ring) <= 8:
            continue
        if any(tuple(sorted((u, v))) in rigid_edges for u, v in zip(ring, ring[1:] + ring[:1])):
            continue
        if any(len(members & set(adjacency[u])) != 2 for u in ring):
            continue
        branches = []
        visited = set(members)
        valid = True
        for center in ring:
            for start in sorted(adjacency[center]):
                if start in visited:
                    continue
                component, frontier = {start}, [start]
                while frontier:
                    u = frontier.pop()
                    for v in adjacency[u]:
                        if v not in members and v not in component:
                            component.add(v)
                            frontier.append(v)
                boundary = {v for u in component for v in adjacency[u] if v in members}
                if boundary != {center}:
                    valid = False
                visited |= component
                branches.append((center, tuple(sorted(component))))
        required = (members & pinned) | {
            center for center, atoms in branches if set(atoms) & pinned
        }
        if not valid or len(required) > 1:
            continue
        if required:
            anchor = min(required)
        elif branches:
            anchor = min(branches, key=lambda pair: (-len(pair[1]), pair[0]))[0]
        else:
            anchor = min(ring)
        result.append(PendantRing(ring, anchor, tuple(branches)))
    return tuple(result)


def _local_frame(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    length = np.linalg.norm(left)
    if length < 1e-10:
        raise ValueError("ring neighbour frame is degenerate")
    first = left / length
    second = right - np.dot(right, first) * first
    norm = np.linalg.norm(second)
    if norm < 1e-10:
        raise ValueError("ring neighbour frame is degenerate")
    second /= norm
    return np.column_stack((first, second, np.cross(first, second)))


def invert_pendant_ring(coords: np.ndarray, part: PendantRing) -> np.ndarray:
    """Invert ring puckering while transporting external branches by proper rotations.

    The ring backbone is reflected in the plane of the attachment vertex and
    its two ring neighbours. These three positions and the attached core stay
    fixed. Each other branch follows the proper rotation between old and new
    local neighbour frames. Thus backbone lengths/angles and local tetrahedral
    hands survive, although remote contacts can change and still need auditing.
    """
    xyz = np.asarray(coords, dtype=float)
    if xyz.ndim != 2 or xyz.shape[1] != 3 or not np.isfinite(xyz).all():
        raise ValueError("coords must be finite N by 3 coordinates")
    ring = part.ring
    pos = ring.index(part.anchor)
    previous, following = ring[pos - 1], ring[(pos + 1) % len(ring)]
    origin = xyz[part.anchor]
    frame = _local_frame(xyz[previous] - origin, xyz[following] - origin)
    normal = frame[:, 2]
    out = xyz.copy()
    indices = list(ring)
    vectors = xyz[indices] - origin
    out[indices] -= 2 * np.outer(vectors @ normal, normal)
    # Avoid round-off changes to immutable coordinates.
    anchors = [part.anchor, previous, following]
    out[anchors] = xyz[anchors]
    for center, atoms in part.branches:
        if center == part.anchor:
            continue
        i = ring.index(center)
        left, right = ring[i - 1], ring[(i + 1) % len(ring)]
        old = _local_frame(xyz[left] - xyz[center], xyz[right] - xyz[center])
        new = _local_frame(out[left] - out[center], out[right] - out[center])
        rotation = new @ old.T
        out[list(atoms)] = out[center] + (xyz[list(atoms)] - xyz[center]) @ rotation.T
    return out
