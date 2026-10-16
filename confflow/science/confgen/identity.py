"""Conformer identity: when are two structures the same conformer (ConfGen search and refine).

Owns the molecule's graph symmetry (colour-refinement classes and, when enumerable, the
automorphisms of the compared-atom graph), the permutation-aware RMSD built on it, a
fingerprint whose distance profiles give a rigorous RMSD lower bound that skips most RMSDs, and
``MinimaPool``, the de-duplicating store.  Two structures are one conformer when their best RMSD
over graph automorphisms is below ``r_tol`` (default ``R_TOL``) and, when both energies are
known, those agree within ``E_TOL``.  The RMSD covers heavy atoms, hydrogens on heteroatoms and
hydrogens on a non-covalent (reactive, coordination) edge.  Mirror images count as one conformer
by default, as in CREST/GOAT; in the search a mirror image that inverts a declared stereocentre
never reaches the pool because the audit rejects it.

Pure: no I/O, no processes.  Uses RDKit only to enumerate automorphisms and SciPy for the
assignment fallback when the automorphism group is too large.
"""

from __future__ import annotations

import hashlib
import pickle
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from confflow.science.confgen.graph import TypedGraph

__all__ = [
    "E_TOL",
    "R_TOL",
    "HARTREE_TO_KCAL",
    "Identity",
    "Minimum",
    "MinimaPool",
    "full_adjacency",
]

HARTREE_TO_KCAL = 627.5094740631
#: kcal/mol: larger energy differences are always different minima.
E_TOL = 0.5
#: Angstrom: best automorphism RMSD below this is the same minimum.
R_TOL = 0.2
_AUTO_CAP = 60000
_ANCHOR_TOL = 0.5  # Angstrom: anchor-triple distance consistency in the fallback RMSD
_ANCHOR_CAP = 2000  # anchor-triple images tried at most
#: distinct RDKit bond types per edge kind, so graph matches never swap edges of different kinds
_EDGE_BOND = {
    "COVALENT": "SINGLE",
    "COORDINATION": "DOUBLE",
    "FORMING": "TRIPLE",
    "BREAKING": "QUADRUPLE",
}


def typed_mol(
    elements: Sequence[str],
    full: list[dict[int, str]],
    atoms: Sequence[int],
    isotopes: Sequence[int],
) -> Any:
    """Return an RDKit molecule of ``atoms`` whose bond types carry the typed-graph edge kinds.

    ``isotopes`` colours the atoms (e.g. by folded hydrogen count); self-matches of the result are
    exactly the automorphisms of the typed graph restricted to ``atoms``.
    """
    from rdkit import Chem

    where = {int(a): k for k, a in enumerate(atoms)}
    rw = Chem.RWMol()
    for a, iso in zip(atoms, isotopes):
        atom = Chem.Atom(elements[int(a)])
        atom.SetIsotope(int(iso))
        atom.SetNoImplicit(True)
        rw.AddAtom(atom)
    for a in atoms:
        for b, kind in full[int(a)].items():
            if b in where and int(a) < b:
                rw.AddBond(where[int(a)], where[b], getattr(Chem.BondType, _EDGE_BOND[kind]))
    return rw.GetMol()


def full_adjacency(graph: TypedGraph) -> list[dict[int, str]]:
    """Return per-atom neighbour maps over every edge kind plus the reaction pairs."""
    n = graph.natoms
    full: list[dict[int, str]] = [dict() for _ in range(n)]
    for edge in graph.edges:
        full[edge.a][edge.b] = full[edge.b][edge.a] = edge.type.value
    for a, b in graph.reaction_pairs:
        full[int(a)].setdefault(int(b), "FORMING")
        full[int(b)].setdefault(int(a), "FORMING")
    return full


def _kabsch_msd(p: np.ndarray, q: np.ndarray) -> float:
    """Mean square deviation of centred ``p`` onto centred ``q`` under the best proper rotation."""
    u, s, vt = np.linalg.svd(p.T @ q)
    if np.linalg.det(u @ vt) < 0:
        s = s.copy()
        s[2] = -s[2]
    return float(max((p**2).sum() + (q**2).sum() - 2.0 * s.sum(), 0.0) / len(p))


def _kabsch_rotation(p: np.ndarray, q: np.ndarray) -> np.ndarray:
    """Proper rotation ``r`` minimising |p @ r - q| for point sets about the origin."""
    u, _, vt = np.linalg.svd(p.T @ q)
    d = np.eye(3)
    d[2, 2] = np.sign(np.linalg.det(u @ vt)) or 1.0
    return u @ d @ vt


@dataclass(frozen=True, slots=True)
class Minimum:
    """One unique minimum: energy (Eh), coordinates, search label, origin and bookkeeping."""

    id: int
    energy: float
    coords: np.ndarray
    label: str
    origin: str
    cell: str = ""
    fingerprint: np.ndarray = field(default_factory=lambda: np.zeros(0), repr=False)


class Identity:
    """Graph symmetry and same-minimum test for one molecule."""

    def __init__(self, graph: TypedGraph, r_tol: float = R_TOL, mirror: bool = True) -> None:
        self.graph = graph
        self.r_tol = float(r_tol)
        self.mirror = mirror
        self.elements = [atom.element for atom in graph.atoms]
        self.n = len(self.elements)
        self.metal = graph.metal_center
        self.full = full_adjacency(graph)
        self.classes = self._refine()
        self.kept = np.array(
            [
                i
                for i, e in enumerate(self.elements)
                if e != "H"
                or any(self.elements[k] not in ("C", "H") for k in self.full[i])
                or any(kind != "COVALENT" for kind in self.full[i].values())
            ]
        )
        self._kept_groups = [
            np.flatnonzero(np.array([self.classes[int(a)] for a in self.kept]) == c)
            for c in sorted({self.classes[int(a)] for a in self.kept})
        ]
        self._single = np.array([g[0] for g in self._kept_groups if len(g) == 1], dtype=int)
        self._multi = [g for g in self._kept_groups if len(g) > 1]
        self._autos: np.ndarray | None | bool = False  # False = not computed yet
        self._nbrs: list[dict[int, str]] | None = None

    def _refine(self, rounds: int = 12) -> list[int]:
        colour = [hash(e) for e in self.elements]
        for _ in range(rounds):
            colour = [
                hash((colour[i], tuple(sorted((colour[j], t) for j, t in self.full[i].items()))))
                for i in range(self.n)
            ]
        ids: dict[int, int] = {}
        return [ids.setdefault(c, len(ids)) for c in colour]

    # -- automorphisms of the kept-atom graph (C-H hydrogens folded into their carbon)
    def automorphisms(self) -> np.ndarray | None:
        """Return all automorphisms as rows of kept-atom positions, or ``None`` past the cap."""
        if self._autos is False:
            where = {int(a) for a in self.kept}
            folded = [1 + sum(1 for k in self.full[int(a)] if k not in where) for a in self.kept]
            mol = typed_mol(self.elements, self.full, [int(a) for a in self.kept], folded)
            found = mol.GetSubstructMatches(
                mol, uniquify=False, useChirality=False, maxMatches=_AUTO_CAP + 1
            )
            self._autos = None if len(found) > _AUTO_CAP else np.array(found, dtype=int)
        return self._autos  # type: ignore[return-value]

    def rmsd(self, x: np.ndarray, y: np.ndarray, mirror: bool | None = None) -> float:
        """Return the best RMSD of ``x`` onto ``y`` over automorphisms (and mirror images).

        ``mirror`` defaults to the Identity's policy; the mirror image is only tried while the
        direct RMSD is not already below ``r_tol``.
        """
        best = self._rmsd(x, y)
        if (self.mirror if mirror is None else mirror) and best >= self.r_tol:
            best = min(best, self._rmsd(x * np.array([-1.0, 1.0, 1.0]), y))
        return best

    def _rmsd(self, x: np.ndarray, y: np.ndarray) -> float:
        p = x[self.kept] - x[self.kept].mean(0)
        q = y[self.kept] - y[self.kept].mean(0)
        perms = self.automorphisms()
        if perms is None:
            return self._rmsd_assigned(p, q)
        qp = q[perms]
        h = np.einsum("ni,pnj->pij", p, qp)
        u, s, vt = np.linalg.svd(h)
        d = np.sign(np.linalg.det(u @ vt))
        s[:, 2] *= d
        msd = np.maximum((p**2).sum() + (q**2).sum() - 2.0 * s.sum(1), 0.0) / len(p)
        return float(np.sqrt(msd.min()))

    def _rmsd_assigned(self, p: np.ndarray, q: np.ndarray) -> float:
        """Fallback for huge automorphism groups: the best of a set of legal graph mappings.

        Every mapping tried preserves the typed kept-atom graph (classes, adjacency, edge kinds),
        so the result is never below the true symmetry RMSD (no false merges).  Three anchor atoms
        from the smallest classes are matched to every distance-consistent image triple; each
        fixes a rotation, then a breadth-first walk from the first anchor maps neighbours onto
        neighbours of the same class and edge kind by nearest assignment; the fit is refined once.
        """
        cls = [self.classes[int(a)] for a in self.kept]
        groups: dict[int, list[int]] = defaultdict(list)
        for pos, c in enumerate(cls):
            groups[c].append(pos)
        best = _kabsch_msd(p, q)  # the identity mapping is always legal
        anchors = self._anchors(p, cls, groups)
        if len(anchors) < 3:
            return float(np.sqrt(best))
        a, b, c = anchors
        dab, dac, dbc = (np.linalg.norm(p[x] - p[y]) for x, y in ((a, b), (a, c), (b, c)))
        tried = 0
        for ia in groups[cls[a]]:
            for ib in groups[cls[b]]:
                if ib == ia or abs(np.linalg.norm(q[ia] - q[ib]) - dab) > _ANCHOR_TOL:
                    continue
                for ic in groups[cls[c]]:
                    if ic in (ia, ib) or abs(np.linalg.norm(q[ia] - q[ic]) - dac) > _ANCHOR_TOL:
                        continue
                    if abs(np.linalg.norm(q[ib] - q[ic]) - dbc) > _ANCHOR_TOL:
                        continue
                    tried += 1
                    if tried > _ANCHOR_CAP:
                        return float(np.sqrt(best))
                    rot = _kabsch_rotation(p[[a, b, c]], q[[ia, ib, ic]])
                    for _ in range(2):
                        perm = self._walk(p @ rot, q, a, ia, cls)
                        if perm is None:
                            break
                        best = min(best, _kabsch_msd(p, q[perm]))
                        rot = _kabsch_rotation(p, q[perm])
        return float(np.sqrt(best))

    def _anchors(self, p: np.ndarray, cls: list[int], groups: dict[int, list[int]]) -> list[int]:
        """Three non-collinear kept positions from the smallest symmetry classes."""
        anchors: list[int] = []
        for i in sorted(range(len(p)), key=lambda i: (len(groups[cls[i]]), i)):
            if not anchors or (len(anchors) == 1 and np.linalg.norm(p[i] - p[anchors[0]]) > 0.5):
                anchors.append(i)
            elif len(anchors) == 2:
                u, v = p[anchors[1]] - p[anchors[0]], p[i] - p[anchors[0]]
                if np.linalg.norm(np.cross(u, v)) > 0.25 * np.linalg.norm(u) * np.linalg.norm(v):
                    anchors.append(i)
                    break
        return anchors

    def _walk(
        self, pr: np.ndarray, q: np.ndarray, root: int, image: int, cls: list[int]
    ) -> np.ndarray | None:
        """Legal kept-graph mapping grown from ``root -> image`` by nearest neighbour assignment."""
        from scipy.optimize import linear_sum_assignment

        nbr = self._kept_nbrs()
        mapped, used, queue = {root: image}, {image}, [root]
        while queue:
            u = queue.pop(0)
            src: dict[tuple[int, str], list[int]] = defaultdict(list)
            dst: dict[tuple[int, str], list[int]] = defaultdict(list)
            for v, kind in nbr[u].items():
                if v not in mapped:
                    src[(cls[v], kind)].append(v)
            for w, kind in nbr[mapped[u]].items():
                if w not in used:
                    dst[(cls[w], kind)].append(w)
            for key, vs in src.items():
                ws = dst.get(key, [])
                if len(ws) < len(vs):
                    return None
                cost = ((pr[vs][:, None, :] - q[ws][None, :, :]) ** 2).sum(-1)
                r, c = linear_sum_assignment(cost)
                for k, m in zip(r, c):
                    mapped[vs[k]] = ws[m]
                    used.add(ws[m])
                    queue.append(vs[k])
        if len(mapped) != len(pr):
            return None  # disconnected kept graph: no single walk covers it
        perm = np.array([mapped[k] for k in range(len(pr))])
        for u, row in enumerate(nbr):
            for v, kind in row.items():
                if nbr[int(perm[u])].get(int(perm[v])) != kind:
                    return None  # a ring closure broke an edge: not a graph mapping
        return perm

    def _kept_nbrs(self) -> list[dict[int, str]]:
        """Typed adjacency among kept atoms, by kept position (cached)."""
        if self._nbrs is None:
            where = {int(a): k for k, a in enumerate(self.kept)}
            self._nbrs = [
                {where[b]: kind for b, kind in self.full[int(a)].items() if b in where}
                for a in self.kept
            ]
        return self._nbrs

    # -- cheap invariants
    def fingerprint(self, x: np.ndarray) -> np.ndarray:
        """Return the kept-atom distance profiles, flattened (rigid- and mirror-invariant).

        A kept atom's profile is its distances to the kept atoms of each class, sorted within
        the class; ``_bound`` turns two sets of profiles into a lower bound on the RMSD.
        """
        p = x[self.kept]
        d = np.linalg.norm(p[:, None] - p[None], axis=2)
        rows = [np.sort(d[:, g], axis=1) for g in self._kept_groups]  # one row per kept atom
        return np.concatenate(rows, axis=1).ravel()

    def _bound(self, f1: np.ndarray, f2: np.ndarray) -> float:
        """Lower bound on ``rmsd`` (any automorphism, either mirror image) from two fingerprints.

        A class-preserving mapping pi aligned with residuals e_i gives
        |d_ij - d'_pi(i)pi(j)| <= |e_i - e_j|, and sum_{i<j} |e_i - e_j|^2 <= m^2 RMSD^2 over the
        m kept atoms.  Atom i's row of that sum is at least the squared distance between the
        class-sorted profiles of i and pi(i) (rearrangement inequality), so half the cheapest
        class-preserving assignment of profiles bounds the whole sum from below.
        """
        from scipy.optimize import linear_sum_assignment

        n = len(self.kept)
        a = f1.reshape(n, -1)
        b = f2.reshape(n, -1)
        total = float(((a[self._single] - b[self._single]) ** 2).sum())
        for g in self._multi:
            cost = ((a[g][:, None] - b[g][None]) ** 2).sum(2)
            r, c = linear_sum_assignment(cost)
            total += float(cost[r, c].sum())
        return float(np.sqrt(total / 2.0)) / n

    def same(self, e1: float, x1: np.ndarray, f1: np.ndarray, m: Minimum) -> bool:
        """Return whether (energy, coords, fingerprint) is the minimum ``m``."""
        return self.close(e1, f1, m.energy, m.fingerprint) and self.rmsd(x1, m.coords) < self.r_tol

    def close(self, e1: float, f1: np.ndarray, e2: float, f2: np.ndarray) -> bool:
        """Return whether the cheap tests leave the RMSD to decide (``same`` without it).

        A NaN energy (unknown) skips the energy test.
        """
        if abs(e1 - e2) * HARTREE_TO_KCAL > E_TOL:
            return False
        return self._bound(f1, f2) < self.r_tol


_WORKER_IDENTITY: dict[str, Identity] = {}


def _rmsd_task(
    args: tuple[str, TypedGraph, float, bool, list[tuple[np.ndarray, np.ndarray]]],
) -> list[bool]:
    """Worker: ``rmsd < r_tol`` for each pair, with the Identity built once per process."""
    key, graph, r_tol, mirror, pairs = args
    idn = _WORKER_IDENTITY.get(key)
    if idn is None:
        _WORKER_IDENTITY.clear()
        idn = _WORKER_IDENTITY[key] = Identity(graph, r_tol, mirror)
    return [idn.rmsd(x, y) < r_tol for x, y in pairs]


class MinimaPool:
    """De-duplicating store of minima; a rediscovery keeps the lower energy and its geometry."""

    def __init__(self, identity: Identity) -> None:
        self.identity = identity
        self.minima: list[Minimum] = []
        self.hits: list[int] = []

    def __len__(self) -> int:
        return len(self.minima)

    def add(
        self, energy: float, coords: np.ndarray, label: str, origin: str, cell: str = ""
    ) -> int | None:
        """Add a minimum; return its new id, or ``None`` if it rediscovers a known one."""
        fp = self.identity.fingerprint(coords)
        return self.commit(self.match(energy, coords, fp), energy, coords, label, origin, cell, fp)

    def match(self, energy: float, coords: np.ndarray, fp: np.ndarray | None = None) -> int | None:
        """Return the index of the first member that (energy, coords) rediscovers, or ``None``."""
        fp = self.identity.fingerprint(coords) if fp is None else fp
        same = self.identity.same
        return next((k for k, m in enumerate(self.minima) if same(energy, coords, fp, m)), None)

    def commit(
        self, hit: int | None, energy: float, coords: np.ndarray, label: str, origin: str,
        cell: str, fp: np.ndarray,
    ) -> int | None:  # fmt: skip
        """Record a rediscovery of member ``hit`` (``None``: a new minimum); return the new id."""
        if hit is not None:
            m = self.minima[hit]
            self.hits[hit] += 1
            if energy < m.energy:
                self.minima[hit] = Minimum(m.id, energy, coords, m.label, m.origin, m.cell, fp)
            return None
        rec = Minimum(len(self.minima), energy, coords, label, origin, cell, fp)
        self.minima.append(rec)
        self.hits.append(1)
        return rec.id

    def add_many(
        self, rows: Sequence[tuple[float, np.ndarray, str, str, str]],
        pmap: Callable[[Callable[[Any], Any], Sequence[Any]], Iterable[Any]] | None = None,
        chunks: int = 64,
    ) -> list[int | None]:  # fmt: skip
        """``add`` each (energy, coords, label, origin, cell) row in order, with the same result.

        The RMSDs the cheap tests leave are computed up front (in parallel through ``pmap`` when
        there are enough) against every earlier pool member or row as it stands now; a member
        replaced by a lower-energy rediscovery during the batch is then re-tested serially.
        """
        idn = self.identity
        fps = [idn.fingerprint(r[1]) for r in rows]
        base = len(self.minima)
        states = [(m.energy, m.coords, m.fingerprint) for m in self.minima]
        states += [(r[0], r[1], fp) for r, fp in zip(rows, fps)]
        energy = np.array([st[0] for st in states])
        need = [
            (i, s)
            for i, (r, fp) in enumerate(zip(rows, fps))
            for s in np.flatnonzero(np.abs(energy[: base + i] - r[0]) * HARTREE_TO_KCAL <= E_TOL)
            if idn.close(r[0], fp, states[s][0], states[s][2])
        ]
        pairs = [(rows[i][1], states[s][1]) for i, s in need]
        if pmap is None or len(need) < 4 * chunks:
            flat = [idn.rmsd(x, y) < idn.r_tol for x, y in pairs]
        else:
            key = hashlib.sha256(pickle.dumps((idn.graph, idn.r_tol, idn.mirror))).hexdigest()
            size = -(-len(pairs) // chunks)
            tasks = [
                (key, idn.graph, idn.r_tol, idn.mirror, pairs[k : k + size])
                for k in range(0, len(pairs), size)
            ]
            flat = [v for out in pmap(_rmsd_task, tasks) for v in out]
        verdict = dict(zip(need, flat))
        trues: dict[int, set[int]] = defaultdict(set)
        for (i, s), v in verdict.items():
            if v:
                trues[i].add(s)
        index_of = {k: k for k in range(base)}  # precomputed state -> pool index
        stale: set[int] = set()  # pool indices replaced during the batch
        out: list[int | None] = []
        for i, (r, fp) in enumerate(zip(rows, fps)):
            known = {index_of[s] for s in trues[i] if s in index_of} - stale
            hit = self._first(r, fp, known, stale)
            mid = self.commit(hit, *r, fp)
            if mid is not None:
                index_of[base + i] = mid
            elif hit is not None and self.minima[hit].coords is r[1]:
                stale.add(hit)
            out.append(mid)
        return out

    def _first(
        self, row: tuple[float, np.ndarray, str, str, str], fp: np.ndarray,
        known: set[int], stale: set[int],
    ) -> int | None:  # fmt: skip
        """First pool index matching ``row``: ``known`` (precomputed) or a re-tested stale one."""
        first = min(known, default=len(self.minima))
        for k in sorted(stale):
            if k < first and self.identity.same(row[0], row[1], fp, self.minima[k]):
                return k
        return first if first < len(self.minima) else None

    def lowest(self) -> float:
        """Return the lowest energy (Eh); the pool must not be empty."""
        return min(m.energy for m in self.minima)

    def within(self, window_kcal: float) -> list[Minimum]:
        """Return the minima within ``window_kcal`` of the lowest, lowest first."""
        if not self.minima:
            return []
        lo = self.lowest()
        rows = [m for m in self.minima if (m.energy - lo) * HARTREE_TO_KCAL <= window_kcal]
        return sorted(rows, key=lambda m: m.energy)

    def ordered(self) -> list[Minimum]:
        """Return every minimum, lowest first."""
        return sorted(self.minima, key=lambda m: m.energy)
