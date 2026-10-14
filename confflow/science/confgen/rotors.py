"""Torsional degrees of freedom of a molecule, derived from its typed graph alone.

Owns rotatable-bond perception (bridges of the full graph that are single covalent bonds
between sp2/sp3 centres, or metal-donor coordination bonds; methyl-like symmetric tops are
skipped), each rotor's symmetry period and grid of moves, rigid rotation of the moving side,
dihedrals, small-ring torsion signatures, and the steric clash test the search applies to
generated geometries.

Pure: no I/O.  Bond orders and hybridisation come from the same RDKit perception the DG
seeder uses, so both agree on what is a single bond.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import reduce

import numpy as np

from confflow.science.confgen import dg_seed
from confflow.science.confgen.identity import Identity

__all__ = ["Rotor", "Topology", "dihedral", "perceive_mol", "rotate"]


@dataclass(frozen=True, slots=True)
class Rotor:
    """A rotatable bond: ``axis`` (fixed atom, pivot), the atoms that turn, its move angles."""

    bond: tuple[int, int]
    axis: tuple[int, int]
    moving: tuple[int, ...]
    moves: tuple[float, ...]
    kind: str
    period: float
    #: Four atoms defining the rotor's dihedral (neighbour, axis[0], axis[1], neighbour).
    torsion: tuple[int, int, int, int]

    def with_moves(self, moves: tuple[float, ...]) -> Rotor:
        """Return a copy with a different move set."""
        return Rotor(self.bond, self.axis, self.moving, moves, self.kind, self.period, self.torsion)


def perceive_mol(
    elements: list[str], graph_bonds: set[tuple[int, int]], metal: int | None, reference: np.ndarray
):  # type: ignore[no-untyped-def]
    """Return an RDKit molecule with perceived bond orders and charges (metal left bare)."""
    from rdkit import Chem

    orders: dict[tuple[int, int], object] = {}
    charges: dict[int, int] = {}
    for frag in dg_seed._split_fragments(len(elements), metal, graph_bonds):
        o, c, _ = dg_seed._perceive_fragment(
            frag, graph_bonds, elements, reference, dg_seed._CHARGE_ORDER, explicit=False
        )
        orders.update(o)
        charges.update(c)
    rw = Chem.RWMol()
    for i, e in enumerate(elements):
        atom = Chem.Atom(e)
        atom.SetFormalCharge(charges.get(i, 0))
        atom.SetNoImplicit(True)
        rw.AddAtom(atom)
    for (i, j), order in sorted(orders.items()):
        rw.AddBond(i, j, order)
    mol = rw.GetMol()
    mol.UpdatePropertyCache(strict=False)
    flags = Chem.SanitizeFlags.SANITIZE_ALL ^ Chem.SanitizeFlags.SANITIZE_PROPERTIES
    Chem.SanitizeMol(mol, sanitizeOps=flags)
    return mol


def rotate(xyz: np.ndarray, rotor: Rotor, angle: float) -> np.ndarray:
    """Return ``xyz`` with the rotor's moving side turned by ``angle`` degrees about its axis."""
    p, q = xyz[rotor.axis[0]], xyz[rotor.axis[1]]
    k = (q - p) / np.linalg.norm(q - p)
    t = math.radians(angle)
    out = xyz.copy()
    idx = list(rotor.moving)
    v = xyz[idx] - q
    out[idx] = (
        q + v * math.cos(t) + np.cross(k, v) * math.sin(t) + np.outer(v @ k, k) * (1 - math.cos(t))
    )
    return out


def dihedral(x: np.ndarray, a: int, b: int, c: int, d: int) -> float:
    """Return the dihedral a-b-c-d in degrees (-180, 180]."""
    b0, b1, b2 = x[a] - x[b], x[c] - x[b], x[d] - x[c]
    b1 = b1 / np.linalg.norm(b1)
    v, w = b0 - b0.dot(b1) * b1, b2 - b2.dot(b1) * b1
    return math.degrees(math.atan2(float(np.cross(b1, v).dot(w)), float(v.dot(w))))


class Topology:
    """Rotors, small rings and steric exclusions of one molecule."""

    def __init__(
        self,
        identity: Identity,
        reference: np.ndarray,
        graph_bonds: set[tuple[int, int]],
        radii: np.ndarray,
    ) -> None:
        self.id = identity
        self.full = identity.full
        self.n, self.metal = identity.n, identity.metal
        self.radii = np.asarray(radii, dtype=float)
        self._rsum = self.radii[:, None] + self.radii[None]
        excl = np.eye(self.n, dtype=bool)
        for i in range(self.n):
            for j in self.full[i]:
                excl[i, j] = True
                for k in self.full[j]:
                    excl[i, k] = True
        if self.metal is not None:
            excl[self.metal, :] = excl[:, self.metal] = True
        self._excl = excl
        self.rings = self._rings()
        try:
            mol = perceive_mol(identity.elements, graph_bonds, self.metal, reference)
        except Exception:  # perception failure leaves the molecule without torsional moves
            mol = None
        self.rotors: tuple[Rotor, ...] = self._rotors(mol) if mol is not None else ()

    # -- perception
    def _bridges(self) -> set[tuple[int, int]]:
        out: set[tuple[int, int]] = set()
        disc: dict[int, int] = {}
        low: dict[int, int] = {}
        clock = 0
        for root in range(self.n):
            if root in disc:
                continue
            stack = [(root, -1, iter(self.full[root]))]
            disc[root] = low[root] = clock
            clock += 1
            while stack:
                u, parent, it = stack[-1]
                nxt = next(it, None)
                if nxt is None:
                    stack.pop()
                    if parent >= 0:
                        low[parent] = min(low[parent], low[u])
                        if low[u] > disc[parent]:
                            out.add((min(u, parent), max(u, parent)))
                    continue
                if nxt == parent:
                    continue
                if nxt in disc:
                    low[u] = min(low[u], disc[nxt])
                else:
                    disc[nxt] = low[nxt] = clock
                    clock += 1
                    stack.append((nxt, u, iter(self.full[nxt])))
        return out

    def _rings(self) -> list[list[int]]:
        from rdkit import Chem

        rw = Chem.RWMol()
        for e in self.id.elements:
            rw.AddAtom(Chem.Atom(e))
        for i in range(self.n):
            for j in self.full[i]:
                if i < j:
                    rw.AddBond(i, j, Chem.BondType.SINGLE)
        return [list(r) for r in Chem.GetSymmSSSR(rw.GetMol()) if len(r) <= 9]

    def _rotors(self, mol) -> tuple[Rotor, ...]:  # type: ignore[no-untyped-def]
        from rdkit import Chem

        hyb = {
            Chem.HybridizationType.SP3: 3,
            Chem.HybridizationType.SP2: 2,
            Chem.HybridizationType.SP: 1,
        }
        cls, full = self.id.classes, self.full
        found: list[Rotor] = []
        for a, b in sorted(self._bridges()):
            kind = full[a][b]
            if kind not in ("COVALENT", "COORDINATION"):
                continue
            if kind == "COVALENT":
                bond = mol.GetBondBetweenAtoms(a, b)
                if bond is None or bond.GetBondType() != Chem.BondType.SINGLE:
                    continue
            if len(full[a]) < 2 or len(full[b]) < 2:
                continue
            folds, kinds, top = [], [], False
            for x, y in ((a, b), (b, a)):
                others = [k for k in full[x] if k != y]
                same = len({cls[k] for k in others}) == 1
                if same and len(others) == 3 and all(len(full[k]) == 1 for k in others):
                    top = True  # methyl-like symmetric top
                folds.append(len(others) if same and len(others) in (2, 3) else 1)
                kinds.append(
                    3 if x == self.metal else hyb.get(mol.GetAtomWithIdx(x).GetHybridization(), 0)
                )
            if top or 1 in kinds or 0 in kinds:
                continue
            nmin = 3 if kinds == [3, 3] else 2 if kinds == [2, 2] else 6
            period = 360.0 / reduce(math.lcm, folds)
            step = 360.0 / nmin
            moves = tuple(k * step for k in range(1, nmin) if k * step < period - 1e-6)
            if not moves:
                continue
            side_b, stack = {b}, [b]
            while stack:
                u = stack.pop()
                for v in full[u]:
                    if v not in side_b and not (u == b and v == a):
                        side_b.add(v)
                        stack.append(v)
            if a in side_b:
                continue
            side_a = set(range(self.n)) - side_b
            if self.metal in side_b or (self.metal not in side_a and len(side_b) > len(side_a)):
                axis, moving = (b, a), tuple(sorted(side_a))
            else:
                axis, moving = (a, b), tuple(sorted(side_b))
            na = min(k for k in full[axis[0]] if k != axis[1])
            nb = min(k for k in full[axis[1]] if k != axis[0])
            found.append(
                Rotor((a, b), axis, moving, moves, kind, period, (na, axis[0], axis[1], nb))
            )
        return tuple(found)

    # -- geometry
    def clashes(self, xyz: np.ndarray, scale: float = 0.45) -> bool:
        """Return whether two atoms more than two bonds apart sit closer than ``scale`` x radii."""
        d = np.linalg.norm(xyz[:, None] - xyz[None], axis=-1) / self._rsum
        return bool(d[~self._excl].min(initial=np.inf) < scale)

    def torsion(self, xyz: np.ndarray, rotor: Rotor) -> float:
        """Return the rotor's dihedral in degrees."""
        return dihedral(xyz, *rotor.torsion)

    def ring_signature(self, xyz: np.ndarray) -> str:
        """Return a coarse sign pattern of every small ring's torsions (ring-pucker state)."""
        parts = []
        for r in self.rings:
            k = len(r)
            t = [
                dihedral(xyz, r[i], r[(i + 1) % k], r[(i + 2) % k], r[(i + 3) % k])
                for i in range(k)
            ]
            parts.append("".join("+" if v > 20 else "-" if v < -20 else "0" for v in t))
        return "|".join(parts)
