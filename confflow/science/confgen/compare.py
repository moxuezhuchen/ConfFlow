"""Compare and de-duplicate arbitrary structures of one molecule with the ConfGen identity test.

Plain heavy-atom RMSD treats symmetry-equivalent atoms as different, so a tBu or phenyl flip
makes two copies of one structure look ~1 A apart. This module builds the same
``identity.Identity`` the ConfGen search uses (graph automorphisms, mirror images, distance-profile
lower bound) from a reference geometry alone, so any XYZ set can be compared the same way.

The graph is perceived from the reference by covalent radii (``bond_scale``). With exactly one
metal, its edges are coordination edges; declared reaction pairs become FORMING edges. Every
structure is compared on that one graph.

Pure: no I/O.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from confflow.science.confgen.graph import AtomRef, EdgeType, TypedEdge, TypedGraph
from confflow.science.confgen.identity import E_TOL, HARTREE_TO_KCAL, R_TOL, Identity

__all__ = ["DedupeRow", "dedupe", "pair_rmsd", "structure_identity"]

#: elements never taken as the metal centre when it is detected automatically
_NON_METALS = frozenset("H He B C N O F Ne Si P S Cl Ar Ge As Se Br Kr Sb Te I Xe At Rn".split())


@dataclass(frozen=True, slots=True)
class DedupeRow:
    """One input structure: its representative (itself when unique) and the distance to it."""

    index: int
    representative: int
    rmsd: float | None = None
    delta_kcal: float | None = None


def _metal(elements: Sequence[str], metal: int | None) -> int | None:
    if metal is not None:
        if not 0 <= metal < len(elements):
            raise ValueError(f"metal index {metal} out of range for {len(elements)} atoms")
        return int(metal)
    found = [i for i, e in enumerate(elements) if e not in _NON_METALS]
    return found[0] if len(found) == 1 else None


def structure_identity(
    elements: Sequence[str],
    coords: np.ndarray,
    *,
    bond_scale: float = 1.15,
    metal: int | None = None,
    reaction_pairs: Sequence[tuple[int, int]] = (),
    r_tol: float = R_TOL,
    mirror: bool = True,
    e_tol: float = E_TOL,
) -> Identity:
    """Return the identity test for structures of the molecule ``elements`` (0-based indices).

    ``metal`` ``None`` picks the only metal-like atom, if there is exactly one.
    """
    from confflow.domain.elements import atomic_number
    from confflow.science.bonds import perceive_adjacency

    xyz = np.asarray(coords, dtype=float)
    n = len(elements)
    if xyz.shape != (n, 3):
        raise ValueError(f"coordinates have shape {xyz.shape}, expected ({n}, 3)")
    centre = _metal(elements, metal)
    pairs = []
    for a, b in reaction_pairs:
        a, b = int(a), int(b)
        if not (0 <= a < n and 0 <= b < n) or a == b:
            raise ValueError(f"reaction pair ({a}, {b}) is invalid for {n} atoms")
        pairs.append((min(a, b), max(a, b)))
    forming = set(pairs)
    adjacency = perceive_adjacency([atomic_number(e) for e in elements], xyz, bond_scale=bond_scale)
    edges = []
    for a, row in enumerate(adjacency):
        for b in row:
            if a < b and (a, b) not in forming:
                kind = EdgeType.COORDINATION if centre in (a, b) else EdgeType.COVALENT
                edges.append(TypedEdge(a=a, b=b, type=kind))
    edges += [TypedEdge(a=a, b=b, type=EdgeType.FORMING) for a, b in sorted(forming)]
    atoms = tuple(AtomRef(index=i, element=e) for i, e in enumerate(elements))
    graph = TypedGraph(atoms, tuple(edges), centre, tuple(sorted(forming)), "structure_compare")
    return Identity(graph, r_tol=r_tol, mirror=mirror, e_tol=e_tol)


def pair_rmsd(identity: Identity, x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Return (symmetry-aware RMSD, plain heavy-atom RMSD) of ``x`` onto ``y`` in Angstrom."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    heavy = [i for i, e in enumerate(identity.elements) if e != "H"]
    p = x[heavy] - x[heavy].mean(0)
    q = y[heavy] - y[heavy].mean(0)
    u, _, vt = np.linalg.svd(p.T @ q)
    d = np.sign(np.linalg.det(u @ vt))
    rot = u @ np.diag([1.0, 1.0, d]) @ vt
    plain = float(np.sqrt(((p @ rot - q) ** 2).sum(1).mean()))
    return float(identity.rmsd(x, y)), plain


def dedupe(
    identity: Identity,
    frames: Sequence[np.ndarray],
    energies: Sequence[float | None] | None = None,
) -> list[DedupeRow]:
    """Group ``frames`` into unique structures; return one row per frame, in input order.

    With every energy known, frames are taken lowest first, so each group is represented by its
    lowest-energy member; otherwise they are taken in input order. A frame joins the first
    representative that passes the identity test (energy test only when both energies are known).
    """
    xs = [np.asarray(x, dtype=float) for x in frames]
    es = [math.nan if e is None else float(e) for e in (energies or [None] * len(xs))]
    if len(es) != len(xs):
        raise ValueError("energies and frames differ in length")
    known = all(not math.isnan(e) for e in es)
    order = sorted(range(len(xs)), key=lambda k: es[k]) if known else list(range(len(xs)))
    reps: list[tuple[int, np.ndarray]] = []  # (frame index, fingerprint)
    rows: dict[int, DedupeRow] = {}
    for k in order:
        fp = identity.fingerprint(xs[k])
        row = DedupeRow(k, k)
        for r, rfp in reps:
            if not identity.close(es[k], fp, es[r], rfp):
                continue
            rmsd = float(identity.rmsd(xs[k], xs[r]))
            if rmsd < identity.r_tol:
                both = not (math.isnan(es[k]) or math.isnan(es[r]))
                delta = (es[k] - es[r]) * HARTREE_TO_KCAL if both else None
                row = DedupeRow(k, r, rmsd, delta)
                break
        if row.representative == k:
            reps.append((k, fp))
        rows[k] = row
    return [rows[k] for k in range(len(xs))]
