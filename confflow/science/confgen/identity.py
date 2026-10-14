"""Conformer identity for the ConfGen search: when are two relaxed structures the same minimum.

Owns the molecule's graph symmetry (colour-refinement classes and, when enumerable, the
automorphisms of the heavy-atom graph), the permutation-aware RMSD built on it, the cheap
fingerprint used as a pre-filter, and ``MinimaPool``, the de-duplicating store of minima the
search grows.  Two structures are one minimum when their energies agree within ``E_TOL``, their
principal moments within 5 %, and their best RMSD over graph automorphisms (heavy atoms plus
hydrogens on heteroatoms) is below ``R_TOL``.  Mirror images count as one minimum, as in
CREST/GOAT; a mirror image that inverts a declared stereocentre never reaches the pool because
the audit rejects it.

Pure: no I/O, no processes.  Uses RDKit only to enumerate automorphisms and SciPy for the
assignment fallback when the automorphism group is too large.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

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
_D_PRE = 1.5  # Angstrom: distance-list pre-filter, far looser than R_TOL
_MOMENT_TOL = 0.05
_AUTO_CAP = 60000
_MASS = {
    "H": 1.008, "B": 10.81, "C": 12.011, "N": 14.007, "O": 15.999, "F": 18.998, "Si": 28.086,
    "P": 30.974, "S": 32.06, "Cl": 35.45, "Br": 79.904, "I": 126.904,
}  # fmt: skip


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


def _mass(symbol: str) -> float:
    if symbol in _MASS:
        return _MASS[symbol]
    from rdkit import Chem

    return float(Chem.GetPeriodicTable().GetAtomicWeight(symbol))


def _kabsch_msd(p: np.ndarray, q: np.ndarray) -> float:
    """Mean square deviation of centred ``p`` onto centred ``q`` under the best proper rotation."""
    u, s, vt = np.linalg.svd(p.T @ q)
    if np.linalg.det(u @ vt) < 0:
        s = s.copy()
        s[2] = -s[2]
    return float(max((p**2).sum() + (q**2).sum() - 2.0 * s.sum(), 0.0) / len(p))


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

    def __init__(self, graph: TypedGraph) -> None:
        self.elements = [atom.element for atom in graph.atoms]
        self.n = len(self.elements)
        self.metal = graph.metal_center
        self.full = full_adjacency(graph)
        self.classes = self._refine()
        self._mass = np.array([_mass(e) for e in self.elements])
        groups: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
        for i in range(self.n):
            for j in range(i + 1, self.n):
                ci, cj = sorted((self.classes[i], self.classes[j]))
                groups[(ci, cj)].append((i, j))
        self._pairs = [
            (np.array([p[0] for p in v]), np.array([p[1] for p in v]))
            for _, v in sorted(groups.items())
        ]
        self.kept = np.array(
            [
                i
                for i, e in enumerate(self.elements)
                if e != "H" or any(self.elements[k] not in ("C", "H") for k in self.full[i])
            ]
        )
        self._autos: np.ndarray | None | bool = False  # False = not computed yet

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
            from rdkit import Chem

            where = {int(a): k for k, a in enumerate(self.kept)}
            rw = Chem.RWMol()
            for a in self.kept:
                atom = Chem.Atom(self.elements[int(a)])
                atom.SetIsotope(1 + sum(1 for k in self.full[int(a)] if k not in where))
                atom.SetNoImplicit(True)
                rw.AddAtom(atom)
            for a in self.kept:
                for b in self.full[int(a)]:
                    if b in where and int(a) < b:
                        rw.AddBond(where[int(a)], where[b], Chem.BondType.SINGLE)
            mol = rw.GetMol()
            found = mol.GetSubstructMatches(
                mol, uniquify=False, useChirality=False, maxMatches=_AUTO_CAP + 1
            )
            self._autos = None if len(found) > _AUTO_CAP else np.array(found, dtype=int)
        return self._autos  # type: ignore[return-value]

    def rmsd(self, x: np.ndarray, y: np.ndarray, mirror: bool = True) -> float:
        """Return the best RMSD of ``x`` onto ``y`` over automorphisms (and mirror images)."""
        best = self._rmsd(x, y)
        if mirror and best >= R_TOL:
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
        """Fallback for huge automorphism groups: assignment within symmetry classes."""
        from scipy.optimize import linear_sum_assignment

        cls = [self.classes[int(a)] for a in self.kept]
        groups: dict[int, list[int]] = defaultdict(list)
        for pos, c in enumerate(cls):
            groups[c].append(pos)
        perm = np.arange(len(p))
        for g in groups.values():
            if len(g) > 1:
                ga = np.array(g)
                cost = ((p[ga][:, None, :] - q[ga][None, :, :]) ** 2).sum(-1)
                r, c = linear_sum_assignment(cost)
                perm[ga[r]] = ga[c]
        return float(np.sqrt(_kabsch_msd(p, q[perm])))

    # -- cheap invariants
    def fingerprint(self, x: np.ndarray) -> np.ndarray:
        """Return [3 principal moments | class-sorted interatomic distance lists]."""
        m = self._mass
        y = x - (x * m[:, None]).sum(0) / m.sum()
        inertia = (m * (y * y).sum(1)).sum() * np.eye(3) - (y * m[:, None]).T @ y
        parts = [np.sort(np.linalg.eigvalsh(inertia))]
        parts += [np.sort(np.linalg.norm(x[i] - x[j], axis=1)) for i, j in self._pairs]
        return np.concatenate(parts)

    def same(self, e1: float, x1: np.ndarray, f1: np.ndarray, m: Minimum) -> bool:
        """Return whether (energy, coords, fingerprint) is the minimum ``m``."""
        if abs(e1 - m.energy) * HARTREE_TO_KCAL > E_TOL:
            return False
        f2 = m.fingerprint
        if np.max(np.abs(f1[:3] - f2[:3]) / np.maximum(f2[:3], 1e-9)) > _MOMENT_TOL:
            return False
        d = np.abs(f1[3:] - f2[3:])
        if d.size:
            k = min(len(d), max(8, len(d) // 100))
            if float(np.partition(d, -k)[-k:].mean()) > _D_PRE:
                return False
        return self.rmsd(x1, m.coords) < R_TOL


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
        for k, m in enumerate(self.minima):
            if self.identity.same(energy, coords, fp, m):
                self.hits[k] += 1
                if energy < m.energy:
                    self.minima[k] = Minimum(m.id, energy, coords, m.label, m.origin, m.cell, fp)
                return None
        rec = Minimum(len(self.minima), energy, coords, label, origin, cell, fp)
        self.minima.append(rec)
        self.hits.append(1)
        return rec.id

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
