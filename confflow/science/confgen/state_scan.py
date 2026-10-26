"""Reminder scan: discrete torsion states the pool holds on the other side of the input's rule.

Each structure is relabelled onto the reference by the best proper-rotation automorphism of the
identity graph (kept atoms only). Every heavy-atom dihedral a-b-c-d over the full adjacency whose
central bond joins two non-metal heavy atoms is then classified by the declared-state rule of
``stereo.torsion_states``: "hand" (sign of sin) when ``|sin theta0| >= 0.5``, else "side" (sign of
cos). An element is a candidate when some structure changed state and every structure keeps its
angle at least ``DELTA`` degrees from the rule's boundary, so the flip is a discrete state change
rather than a sweep across the boundary. Candidates that flip in an identical set of structures
form one unit, represented by its most decisive element.

The scan only reports. It never alters the search or the pool.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Sequence
from typing import Any

import numpy as np

from confflow.science.confgen.identity import HARTREE_TO_KCAL, Identity
from confflow.science.confgen.stereo import TORSION_HAND_SIN

__all__ = ["DELTA", "scan_states"]

#: minimum distance (degrees) from the rule's boundary for every structure of a candidate
DELTA = 20.0


def _dihedrals(coords: np.ndarray, quads: np.ndarray) -> np.ndarray:
    """Return dihedrals (degrees) of ``quads`` (K x 4) on one structure (N x 3)."""
    b0 = coords[quads[:, 0]] - coords[quads[:, 1]]
    b1 = coords[quads[:, 2]] - coords[quads[:, 1]]
    b2 = coords[quads[:, 3]] - coords[quads[:, 2]]
    b1n = b1 / np.linalg.norm(b1, axis=-1, keepdims=True)
    v = b0 - (b0 * b1n).sum(-1, keepdims=True) * b1n
    w = b2 - (b2 * b1n).sum(-1, keepdims=True) * b1n
    x = (v * w).sum(-1)
    y = (np.cross(b1n, v) * w).sum(-1)
    return np.degrees(np.arctan2(y, x))


def _heavy_quads(identity: Identity) -> np.ndarray:
    """Return every heavy-atom quad a-b-c-d (K x 4) whose central bond joins non-metal atoms."""
    full, elements, metal = identity.full, identity.elements, identity.metal

    def heavy(i: int) -> bool:
        return elements[i] != "H"

    quads: list[tuple[int, int, int, int]] = []
    for b in range(identity.n):
        for c in full[b]:
            if not (b < c and heavy(b) and heavy(c) and metal not in (b, c)):
                continue
            for a in full[b]:
                for d in full[c]:
                    if a in (b, c) or d in (b, c) or a == d:
                        continue
                    if heavy(a) and heavy(d):
                        quads.append((a, b, c, d))
    return np.array(quads, dtype=int).reshape(-1, 4)


def _declared_set(
    declared: Collection[Sequence[int]], kept: np.ndarray, perms: np.ndarray | None
) -> set[tuple[int, ...]]:
    """Return declared quads, their automorphism images and both traversal orders.

    Relabelling can move a declared torsion onto a symmetry-equivalent element, so the literal
    quad alone would not silence the state the user already fixed.
    """
    pos = {int(a): k for k, a in enumerate(kept)}
    out: set[tuple[int, ...]] = set()
    for quad in declared:
        images = [tuple(int(i) for i in quad)]
        if perms is not None and len(perms) and all(int(i) in pos for i in quad):
            columns = perms[:, [pos[int(i)] for i in quad]]
            images += [tuple(int(kept[j]) for j in row) for row in columns]
        for image in images:
            out.add(image)
            out.add(image[::-1])
    return out


def _relabel(
    reference: np.ndarray, kept: np.ndarray, perms: np.ndarray, coords: np.ndarray
) -> np.ndarray:
    """Return ``coords`` with kept atoms reordered by the best proper-rotation automorphism."""
    p = reference[kept] - reference[kept].mean(axis=0)
    q = coords[kept] - coords[kept].mean(axis=0)
    qp = q[perms]
    u, s, vt = np.linalg.svd(np.einsum("ni,pnj->pij", p, qp))
    s[:, 2] *= np.sign(np.linalg.det(u @ vt))
    best = perms[int(np.argmax(s.sum(axis=1)))]
    out = coords.copy()
    out[kept] = coords[kept[best]]
    return out


def scan_states(
    identity: Identity,
    reference: np.ndarray,
    coords: Sequence[np.ndarray],
    energies_eh: Sequence[float],
    declared: Collection[tuple[int, int, int, int]] = (),
) -> dict[str, Any]:
    """Report discrete torsion-state changes among ``coords`` relative to ``reference``.

    ``aligned`` is True when structures were relabelled onto the reference (False when the
    automorphism group exceeds its cap, or is empty, so no relabelling was attempted). Energies
    are Hartree; ``lowest_*_kcal`` values are relative to the lowest scanned structure. Quads in
    ``declared`` (either order) are excluded because the caller already enforces them. Units are
    sorted by the number of changed structures, largest first.
    """
    structures = [np.asarray(x, dtype=float) for x in coords]
    energies = np.asarray(energies_eh, dtype=float)
    if len(structures) != len(energies):
        raise ValueError("coords and energies_eh must have one entry per structure")
    count = len(structures)
    if count < 2:
        return {"aligned": False, "scanned": count, "units": []}
    perms = identity.automorphisms()
    aligned = perms is not None and len(perms) > 0
    quads = _heavy_quads(identity)
    if not len(quads):
        return {"aligned": aligned, "scanned": count, "units": []}

    ref = np.asarray(reference, dtype=float)
    kept = np.asarray(identity.kept, dtype=int)
    t0 = _dihedrals(ref, quads)
    s0, c0 = np.sin(np.radians(t0)), np.cos(np.radians(t0))
    hand = np.abs(s0) >= TORSION_HAND_SIN
    angles = np.empty((count, len(quads)))
    for i, x in enumerate(structures):
        y = _relabel(ref, kept, perms, x) if aligned else x  # type: ignore[arg-type]
        angles[i] = _dihedrals(y, quads)

    with np.errstate(invalid="ignore"):
        sin_t, cos_t = np.sin(np.radians(angles)), np.cos(np.radians(angles))
        changed = np.where(hand, np.sign(sin_t) != np.sign(s0), np.sign(cos_t) != np.sign(c0))
        absolute = np.abs(angles)
        margin = np.where(
            hand,
            np.minimum(absolute, 180.0 - absolute),
            np.abs(absolute - 90.0),
        )
    discrete = (margin.min(axis=0) >= DELTA) & changed.any(axis=0)

    excluded = _declared_set(declared, kept, perms if aligned else None)
    units: dict[frozenset[int], list[int]] = defaultdict(list)
    for k in np.flatnonzero(discrete):
        units[frozenset(int(i) for i in np.flatnonzero(changed[:, k]))].append(int(k))
    # a unit holding a declared element (or an equivalent one) is a state the caller enforces
    units = {
        members: elements
        for members, elements in units.items()
        if not any(tuple(int(i) for i in quads[k]) in excluded for k in elements)
    }

    floor = float(energies.min())
    rows: list[dict[str, Any]] = []
    for members, elements in sorted(units.items(), key=lambda kv: -len(kv[0])):
        best = max(elements, key=lambda k: abs(s0[k]) if hand[k] else abs(c0[k]))
        idx = np.array(sorted(members), dtype=int)
        others = np.setdiff1d(np.arange(count), idx)
        same = None
        if others.size:
            same = float((energies[others].min() - floor) * HARTREE_TO_KCAL)
        rows.append(
            {
                "structures": len(members),
                "elements": len(elements),
                "atoms": [int(i) for i in quads[best]],
                "kind": "hand" if hand[best] else "side",
                "theta0": round(float(t0[best]), 1),
                "lowest_changed_kcal": float((energies[idx].min() - floor) * HARTREE_TO_KCAL),
                "lowest_same_kcal": same,
                "members": [int(i) for i in idx],
            }
        )
    return {"aligned": aligned, "scanned": count, "units": rows}
