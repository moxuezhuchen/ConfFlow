"""Iterative conformer search: global DG starts plus feedback moves, under a relaxation budget.

Owns the search strategy validated in the 2026-10 prototype study:

1. global starts: DG embeddings for every coordination class kept in scope (by default only the
   input structure's arrangement), then more for the classes that produced minima;
2. well probe: each rotor's moves become the torsion offsets it actually relaxes to on the
   lowest minima of up to ``probes`` ring/class cells, plus the grid moves no measured well
   covers (a well absent on the probed parents stays reachable);
3. feedback rounds with fixed budget shares: more DG starts, single-rotor moves from the lowest
   unexpanded minima (plus the lowest of each cell), local DG re-embedding of a small region,
   and - once single-rotor moves stop finding new minima - displacements along the softest
   Hessian modes (only when the engine declares ``CAP_HESSIAN``);
4. release: the minima near the lowest are re-relaxed with only reaction-pair restraints and
   published within the energy window.

Every relaxation goes through the backend-neutral ``Calculator``.  CPU-bound RDKit work runs
through an injected ``parallel_map`` so the execution layer decides on processes; the module
itself does no I/O.  Results are reproducible for a fixed seed and the same ``parallel_map``
ordering.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

import numpy as np

from confflow.science.confgen import dg_seed, search
from confflow.science.confgen.calculator import (
    CAP_HESSIAN,
    Calculator,
    DistanceRestraint,
    RelaxRequest,
)
from confflow.science.confgen.coordination import enumeration, shapes
from confflow.science.confgen.graph import CoordinationSpec, TypedGraph
from confflow.science.confgen.identity import HARTREE_TO_KCAL, Identity, MinimaPool, Minimum
from confflow.science.confgen.rotors import Rotor, Topology, perceive_mol, rotate

__all__ = ["ExploreSettings", "ParallelMap", "explore"]

#: ``map``-like callable used for CPU-bound RDKit tasks (results in input order).
ParallelMap = Callable[[Callable[[Any], Any], Sequence[Any]], Iterable[Any]]

_ARR_TOL = 45.0  # deg: largest donor-metal-donor angle change still in the input arrangement
_DONOR_SLACK = 1.3  # an unclassified structure keeps every donor within 1.3 x its ref distance
_RESTRAINT_TOL = 0.03  # Angstrom
_PROBE_MAX_KCAL = 12.0
_LM_KIDS_MODES, _LM_AMPS = 6, (1.2, 2.2)
_SEED_MOD = 2_000_003  # x 1000 stays below 2**31


@dataclass(frozen=True, slots=True)
class ExploreSettings:
    """Search budget and strategy.

    ``budget`` caps the relaxations of the search phase; the release step adds one relaxation
    per minimum within ``window_kcal + 2`` of the lowest, and Hessians are extra.
    """

    budget: int = 1500
    window_kcal: float = 6.0
    seed: int = 1
    starts: int | None = None
    arrangement: Literal["keep", "enumerate"] = "keep"
    batch: int = 64
    probes: int = 3
    #: keep the grid moves that no measured well covers (within 30 degrees)
    wells_keep_grid: bool = True
    share_dg: float = 0.30
    share_single: float = 0.55
    share_reembed: float = 0.15
    share_lowmode: float = 0.10
    lowmode_gate: float = 0.10
    release: bool = True
    #: keep searching past ``min_budget`` while the low-energy region still grows, up to
    #: ``budget``; stop once a trailing stretch found new minima within 3 kcal/mol of the
    #: lowest at no more than ``saturation_rate`` per relaxation and did not lower the minimum
    adaptive: bool = False
    min_budget: int = 1500
    saturation_window: int = 500
    saturation_rate: float = 1.0 / 250.0
    small_ring_torsions: Literal["both", "on", "off"] = "both"
    embed_timeout: int = 20
    fragment_charges: tuple[tuple[int, int], ...] = ()
    bond_scale: float = 1.25


# ----------------------------------------------------------------------------- worker tasks
def _dg_task(args: tuple[Any, ...]) -> tuple[str, list[np.ndarray], str | None, tuple[int, ...]]:
    graph, spec, ref, shape, target, count, seed, st = args
    settings = search.SearchSettings(count, seed, st.small_ring_torsions, st.embed_timeout,
                                     st.fragment_charges, st.bond_scale)  # fmt: skip
    try:
        found = search.generate_starts(graph, spec, ref, shape, [target], settings)
    except search.SearchError as exc:
        return target.id, [], str(exc), ()
    err = found.failures[0][1] if found.failures else None
    return target.id, [s.coords for s in found.starts], err, tuple(found.stereo_centers)


def _reembed_task(args: tuple[Any, ...]) -> tuple[np.ndarray | None, str | None]:
    """Re-embed atoms ``free`` of ``xyz`` by DG; all other atoms are pinned by tight bounds."""
    from rdkit import Chem
    from rdkit.Chem import rdDistGeom
    from rdkit.DistanceGeometry import DoTriangleSmoothing
    from rdkit.Geometry import Point3D

    elements, bonds, metal, xyz, free, pairs, seed, timeout = args
    try:
        mol = Chem.RWMol(perceive_mol(elements, bonds, metal, xyz))
        conf = Chem.Conformer(len(elements))
        for i, p in enumerate(xyz):
            conf.SetAtomPosition(i, Point3D(*map(float, p)))
        mol.AddConformer(conf, assignId=True)
        mol = mol.GetMol()
        Chem.AssignStereochemistryFrom3D(mol)
        bounds = np.asarray(rdDistGeom.GetMoleculeBoundsMatrix(mol), dtype=float)
        n = len(elements)
        d = np.linalg.norm(xyz[:, None] - xyz[None], axis=-1)

        def _set(i: int, j: int, tol: float, target: float | None = None) -> None:
            lo, hi = (i, j) if i < j else (j, i)
            c = d[i, j] if target is None else target
            bounds[lo, hi], bounds[hi, lo] = c + tol, max(0.0, c - tol)

        fixed = [i for i in range(n) if i not in free]
        for a, i in enumerate(fixed):
            for j in fixed[a + 1 :]:
                _set(i, j, 0.03)
        for i, j, tol in pairs:
            if i in free or j in free:
                _set(i, j, tol)
        if not DoTriangleSmoothing(bounds):
            return None, "smoothing"
        params = rdDistGeom.ETKDGv3()
        params.useRandomCoords, params.embedFragmentsSeparately, params.numThreads = True, False, 1
        params.randomSeed, params.timeout = int(seed), int(timeout)
        params.SetBoundsMat(bounds)
        cid = rdDistGeom.EmbedMolecule(mol, params)
        if cid < 0:
            return None, "embed"
        return np.array(mol.GetConformer(cid).GetPositions(), dtype=float), None
    except Exception as exc:  # RDKit raises plain RuntimeErrors on hopeless inputs
        return None, f"error: {str(exc)[:40]}"


# ----------------------------------------------------------------------------- the search
class _Explorer:
    def __init__(self, graph: TypedGraph, spec: CoordinationSpec | None, reference: np.ndarray,
                 shape: str | None, st: ExploreSettings, calc: Calculator, pmap: ParallelMap,
                 should_cancel: Callable[[], bool] | None) -> None:  # fmt: skip
        self.graph, self.spec, self.ref, self.shape, self.st = graph, spec, reference, shape, st
        self.calc, self.pmap, self.should_cancel = calc, pmap, should_cancel
        self.rng = random.Random(f"explore-{st.seed}")
        self.metal = graph.metal_center
        self.id = Identity(graph)
        self.elements = self.id.elements
        self.bonds = dg_seed._candidate_bonds(graph)
        radii = np.array([search._radius(e) for e in self.elements])
        self.topo = Topology(self.id, reference, self.bonds, radii)
        self.rotors: list[Rotor] = list(self.topo.rotors)
        self.donors = (
            list(spec.donor_indices) if spec is not None and self.metal is not None else []
        )
        self.targets = search.plan_targets(graph, spec, shape)
        self.allowed = {t.command for t in self.targets}
        self.tid = {t.command: t.id for t in self.targets}
        self._reduce_targets()
        self.arr_ref: np.ndarray | None = None
        self.arr_perms: list[list[int]] = []
        if st.arrangement == "keep" and self.donors:
            self._keep_arrangement()
        self.restraints = self._restraints()
        react = {(min(int(a), int(b)), max(int(a), int(b))) for a, b in graph.reaction_pairs}
        self.free_restraints = tuple(
            r for r in self.restraints if (min(r.i, r.j), max(r.i, r.j)) in react
        )
        self.re_pairs = [(r.i, r.j, 0.05) for r in self.restraints]
        if self.metal is not None:  # metal to donor neighbours, as the DG seeder pins them
            m = self.metal
            for dn in self.donors:
                self.re_pairs += [(m, k, 0.15) for k in self.id.full[dn]
                                  if k != m and k not in self.donors]  # fmt: skip
        self.centers: tuple[int, ...] = ()
        self.pool = MinimaPool(self.id)
        self.cells: dict[int, str] = {}
        self.marks: dict[str, set[int]] = {}
        self.nopt = 0
        self.fail: Counter[str] = Counter()
        self.yields: Counter[str] = Counter()
        self.spent: Counter[str] = Counter()
        self.found: Counter[str] = Counter()
        self.rate = {"single": 0.5}
        self.history: list[tuple[int, int, float]] = []  # (relaxations, new <3 kcal, lowest)
        self.hessians = 0
        self.released = 0
        self.stop = "budget"  # why the search loop ended: budget | saturated | no_moves | no_minima
        self.release_status = "off"  # off | not_needed | done | failed
        self.dg_round = 0

    # -- setup -----------------------------------------------------------------------------
    def _donor_perms(self) -> list[tuple[int, ...]]:
        """Permutations of the binding sites induced by automorphisms of the typed graph."""
        from rdkit import Chem

        assert self.spec is not None
        full, els = self.id.full, self.elements
        heavy = [i for i, e in enumerate(els) if e != "H"]
        pos = {a: k for k, a in enumerate(heavy)}
        rw = Chem.RWMol()
        for a in heavy:
            atom = Chem.Atom(els[a])
            atom.SetIsotope(1 + sum(els[k] == "H" for k in full[a]))
            atom.SetNoImplicit(True)
            rw.AddAtom(atom)
        for a in heavy:
            for b in full[a]:
                if b in pos and a < b:
                    rw.AddBond(pos[a], pos[b], Chem.BondType.SINGLE)
        mol = rw.GetMol()
        sites = [s.atoms[0] for s in self.spec.binding_sites]
        if any(a not in pos for a in sites):
            return [tuple(range(len(sites)))]
        where = {a: k for k, a in enumerate(sites)}
        perms: set[tuple[int, ...]] = set()
        for match in mol.GetSubstructMatches(
            mol, uniquify=False, useChirality=False, maxMatches=200000
        ):
            image = [heavy[match[pos[a]]] for a in sites]
            if all(a in where for a in image):
                perms.add(tuple(where[a] for a in image))
        return sorted(perms) or [tuple(range(len(sites)))]

    def _reduce_targets(self) -> None:
        """Keep one target per orbit of graph-equivalent donor permutations."""
        if self.spec is None or self.shape is None:
            return
        try:
            perms = self._donor_perms()
        except Exception:  # automorphism search is an optimisation only
            return
        if len(perms) <= 1:
            return
        group = shapes.proper_rotation_group(self.shape)
        rep: dict[tuple[int, ...], str] = {}
        keep = []
        for t in self.targets:
            if t.command in rep:
                continue
            keep.append(t)
            for pm in perms:
                image = tuple(t.placement[pm[i]] for i in range(len(pm)))
                rep.setdefault(enumeration.canonical_representative(image, group), t.id)
        self.tid = {cmd: rep.get(cmd, tid) for cmd, tid in self.tid.items()}
        self.targets = tuple(keep)

    def _angles(self, xyz: np.ndarray) -> np.ndarray:
        assert self.metal is not None
        v = np.array([xyz[d] - xyz[self.metal] for d in self.donors])
        v /= np.linalg.norm(v, axis=1)[:, None]
        return np.degrees(np.arccos(np.clip(v @ v.T, -1.0, 1.0)))

    def _keep_arrangement(self) -> None:
        """Search and accept only the input structure's donor arrangement.

        Applies only when the input itself realises a coordination class of the declared
        shape; a strongly distorted input (no class) keeps every class in scope.
        """
        try:
            self.arr_perms = [list(pm) for pm in self._donor_perms()]
        except Exception:
            self.arr_perms = [list(range(len(self.donors)))]
        rec = search.audit_structure(self.ref, self.graph, self.spec, self.ref, self.shape, (), (),
                                     True, self.st.bond_scale, allowed=self.allowed)  # fmt: skip
        if "coordination_class" in rec.failed_checks or rec.actual_class not in self.tid:
            return  # the input fits no ideal polyhedron: it has no arrangement to keep
        want = self.tid[rec.actual_class]
        self.targets = tuple(t for t in self.targets if self.tid[t.command] == want)
        self.arr_ref = self._angles(self.ref)

    def _same_arrangement(self, xyz: np.ndarray) -> bool:
        if self.arr_ref is None:
            return True
        a = self._angles(xyz)
        return any(float(np.abs(a[np.ix_(pm, pm)] - self.arr_ref).max()) <= _ARR_TOL
                   for pm in self.arr_perms)  # fmt: skip

    def _restraints(self) -> tuple[DistanceRestraint, ...]:
        """Return the reference restraints averaged over graph-equivalent atom pairs."""
        raw = search.distance_restraints(self.graph, self.spec, self.ref)
        cls = self.id.classes
        groups: dict[tuple[int, int], list[float]] = {}
        for r in raw:
            a, b = sorted((cls[r.i], cls[r.j]))
            groups.setdefault((a, b), []).append(r.target)
        out = []
        for r in raw:
            a, b = sorted((cls[r.i], cls[r.j]))
            out.append(DistanceRestraint(r.i, r.j, float(np.mean(groups[(a, b)]))))
        return tuple(out)

    # -- bookkeeping -------------------------------------------------------------------------
    def _poll(self) -> None:
        if self.should_cancel is not None and self.should_cancel():
            raise search.SearchCancelled("search cancelled")

    def _left(self) -> int:
        return max(0, self.st.budget - self.nopt)

    def _rel(self, m: Minimum) -> float:
        return (m.energy - self.pool.lowest()) * HARTREE_TO_KCAL

    def _audit(self, xyz: np.ndarray, conv: bool, free: bool) -> tuple[str | None, tuple[str, ...]]:
        """Return (label, ()) for an accepted structure, else (None, failed checks)."""
        rec = search.audit_structure(xyz, self.graph, self.spec, self.ref, self.shape, (), self.centers,
                                     conv, self.st.bond_scale, allowed=self.allowed if self.shape else None)  # fmt: skip
        bad = [c for c in rec.failed_checks if c != "metal_donor_distance"]
        if not free:
            for r in self.restraints:
                if abs(float(np.linalg.norm(xyz[r.i] - xyz[r.j])) - r.target) > _RESTRAINT_TOL:
                    bad.append("restraint")
                    break
        if not bad:
            if self.shape and not self._same_arrangement(xyz):
                return None, ("arrangement",)
            if self.metal is None or rec.actual_class is None:
                return "t00", ()
            return self.tid.get(rec.actual_class, "unclassified"), ()
        if bad == ["coordination_class"] and self.metal is not None:
            ok = all(
                np.linalg.norm(xyz[d] - xyz[self.metal])
                <= _DONOR_SLACK * np.linalg.norm(self.ref[d] - self.ref[self.metal])
                for d in self.donors
            )
            if not ok:
                return None, ("donor_detached",)
            if not self._same_arrangement(xyz):
                return None, ("arrangement",)
            return "unclassified", ()
        return None, tuple(bad)

    def _relax(self, items: list[tuple[str, np.ndarray, str]], pool: MinimaPool,
               restraints: tuple[DistanceRestraint, ...], free: bool = False) -> list[int]:  # fmt: skip
        """Relax (key, coords, origin) items within budget; add accepted minima; return new ids."""
        self._poll()
        items = items[: self._left()] if not free else items
        reqs = [RelaxRequest(key, x, restraints) for key, x, _ in items]
        outs = self.calc.relax_many(reqs)
        if not free:
            self.nopt += len(items)
        new = []
        for (_, _, origin), out in zip(items, outs):
            if out.coords is None or out.energy_eh is None:
                self.fail["not_converged"] += 1
                continue
            label, bad = self._audit(out.coords, out.converged, free)
            if label is None:
                self.fail.update(bad)
                continue
            cell = label + "#" + self.topo.ring_signature(out.coords)
            mid = pool.add(float(out.energy_eh), out.coords, label, origin, cell)
            if mid is not None:
                new.append(mid)
                self.cells[mid] = cell
        return new

    def _take(self, arm: str, k: int, cells: bool = False) -> list[Minimum]:
        """Lowest unused parents of ``arm`` inside the window.

        With ``cells`` (single-rotor moves), the lowest minimum of each other ring-state cell is
        barred from the arm instead of expanded: those ring states are left to re-embedding.
        Expanding them with single-rotor moves measured worse (oseltamivir 3 seeds, Boltzmann
        weight 74.5 % vs 77.8 %; TS1 re 87 % vs 100 %).  Once no unused parent is left inside
        the window, unused cell minima outside it are handed out instead.
        """
        used = self.marks.setdefault(arm, set())
        lo = self.pool.lowest()
        cand = [m for m in self.pool.ordered() if m.id not in used]
        pick = [m for m in cand if (m.energy - lo) * HARTREE_TO_KCAL <= self.st.window_kcal][:k]
        if cells:
            best: dict[str, Minimum] = {}
            for m in self.pool.minima:
                c = self.cells.get(m.id, "")
                if c not in best or m.energy < best[c].energy:
                    best[c] = m
            extra = [m for m in cand if best.get(self.cells.get(m.id, "")) is m and m not in pick]
            if pick:
                used.update(m.id for m in extra[: max(1, k // 6)])
            else:
                pick = extra[:k]
        used.update(m.id for m in pick)
        return pick

    # -- generators ----------------------------------------------------------------------------
    def _dg(self, plan: list[tuple[Any, int]], tag: str) -> list[tuple[str, np.ndarray, str]]:
        self.dg_round += 1
        # generate_starts derives RDKit seeds as seed * 1000 + small offsets: keep it 32-bit
        base = self.st.seed * 100003 + 7919 * self.dg_round
        tasks = [(self.graph, self.spec, self.ref, self.shape, t, c, (base + 31 * k) % _SEED_MOD, self.st)
                 for k, (t, c) in enumerate(plan)]  # fmt: skip
        items = []
        for tid, coords, err, centers in self.pmap(_dg_task, tasks):
            if centers and not self.centers:
                self.centers = centers
            if err:
                self.fail["dg_embed"] += 1
            for k, xyz in enumerate(coords):
                items.append((f"{tag}/{tid}_{k:02d}", xyz, f"dg:{tid}"))
        return items

    def _single_kids(self, m: Minimum) -> list[tuple[str, np.ndarray]]:
        out = []
        for ri, r in enumerate(self.rotors):
            for ang in r.moves:
                out.append((f"r{ri:02d}_{int(ang):03d}", rotate(m.coords, r, ang)))
        return out

    def _lowmode_kids(self, m: Minimum) -> list[tuple[str, np.ndarray]]:
        from rdkit import Chem

        h = self.calc.hessian(RelaxRequest(f"hess/m{m.id:05d}", m.coords, self.restraints))
        self.hessians += 1
        if h is None:
            self.fail["hessian_failed"] += 1
            return []
        n3 = 3 * len(self.elements)
        pt = Chem.GetPeriodicTable()
        w = np.repeat([pt.GetAtomicWeight(e) for e in self.elements], 3) ** -0.5
        ev, vec = np.linalg.eigh(h * w[:, None] * w[None, :])
        order = np.argsort(np.abs(ev))[6:]  # drop translations and rotations
        soft = sorted(order, key=lambda k: ev[k])[:_LM_KIDS_MODES]
        out = []
        for k in soft:
            d = (vec[:, k] * w).reshape(n3 // 3, 3)
            d /= np.linalg.norm(d, axis=1).max()
            for amp in _LM_AMPS:
                for sg in (1, -1):
                    out.append(
                        (f"lm{k:03d}_{amp:.1f}{'p' if sg > 0 else 'm'}", m.coords + sg * amp * d)
                    )
        return out

    def _moves(self, arm: str, quota: int) -> list[tuple[str, np.ndarray, str]]:
        kids = self._single_kids if arm == "single" else self._lowmode_kids
        items: list[tuple[str, np.ndarray, str]] = []
        while len(items) < quota:
            ps = self._take(arm, 1, cells=arm == "single")
            if not ps:
                break
            for p in ps:
                for tag, x in kids(p):
                    if self.topo.clashes(x):
                        self.fail["move_clash"] += 1
                    else:
                        items.append((f"{arm}/m{p.id:05d}_{tag}", x, f"{arm}:{p.id}"))
        return items

    def _free_sets(self, n: int = 12) -> list[set[int]]:
        pinned = set(self.donors) | ({self.metal} if self.metal is not None else set())
        full = self.id.full
        ring_atoms = {i for r in self.topo.rings for i in r}
        centres = sorted(({i for r in self.rotors for i in r.bond} | ring_atoms) - pinned)
        arms = [
            set(r.moving) for r in self.rotors if len(r.moving) <= 45 and not set(r.moving) & pinned
        ]
        out = []
        for _ in range(n if centres or arms else 0):
            if arms and (not centres or self.rng.random() < 0.4):
                s = set(self.rng.choice(arms))
            else:
                c, depth = self.rng.choice(centres), self.rng.choice((2, 3, 3, 4))
                s, frontier = {c}, {c}
                for _d in range(depth):
                    frontier = {
                        v for u in frontier for v in full[u] if v not in s and v not in pinned
                    }
                    s |= frontier
            s |= {v for u in s for v in full[u] if len(full[v]) == 1}
            out.append(s - pinned)
        return out

    def _reembed(self, quota: int) -> list[tuple[str, np.ndarray, str]]:
        ps = self._take("reembed", max(1, math.ceil(quota / 10)))
        tasks, names = [], []
        for m in ps:
            for k, s in enumerate(self._free_sets()):
                tasks.append((self.elements, self.bonds, self.metal, m.coords, frozenset(s),
                              self.re_pairs, self.rng.randrange(1, 2**30), self.st.embed_timeout))  # fmt: skip
                names.append((f"reembed/m{m.id:05d}_{k:02d}", f"reembed:{m.id}"))
        items = []
        for (name, origin), (x, err) in zip(names, self.pmap(_reembed_task, tasks)):
            if x is None:
                self.fail[f"reembed_{err.split(':')[0]}"] += 1
            else:
                items.append((name, x, origin))
        return items

    def _dg_more(self, quota: int) -> list[tuple[str, np.ndarray, str]]:
        labels = {m.label for m in self.pool.minima}
        good = [t for t in self.targets if t.id in labels] or list(self.targets)
        per = max(1, round(quota / max(1, len(good))))
        if per * len(good) > 1.5 * quota:
            good = self.rng.sample(good, max(1, quota // per))
        return self._dg([(t, per) for t in good], f"dgm{self.dg_round + 1}")

    # -- phases ----------------------------------------------------------------------------------
    def _probe_wells(self) -> None:
        best: dict[str, Minimum] = {}
        for m in self.pool.minima:
            c = self.cells.get(m.id, "")
            if c not in best or m.energy < best[c].energy:
                best[c] = m
        refs = sorted(best.values(), key=lambda m: m.energy)[: self.st.probes]
        items = []
        for ref in refs:
            for ri, r in enumerate(self.rotors):
                for ang in r.moves:
                    x = rotate(ref.coords, r, ang)
                    if not self.topo.clashes(x):
                        items.append(
                            (
                                f"probe/m{ref.id:05d}_r{ri:02d}_{int(ang):03d}",
                                x,
                                f"probe:{ref.id}:{ri}",
                            )
                        )
        scratch = MinimaPool(self.id)
        self._relax_into(items, scratch)
        keep: dict[int, list[float]] = {ri: [] for ri in range(len(self.rotors))}
        byid = {m.id: m for m in refs}
        for m in scratch.minima:
            _, pid, ri_text = m.origin.split(":")
            ref, ri = byid[int(pid)], int(ri_text)
            if (m.energy - ref.energy) * HARTREE_TO_KCAL > _PROBE_MAX_KCAL:
                continue
            r = self.rotors[ri]
            per = r.period
            off = (self.topo.torsion(m.coords, r) - self.topo.torsion(ref.coords, r)) % per

            def circ(a: float, b: float, per: float = per) -> float:
                return min((a - b) % per, (b - a) % per)

            if circ(off, 0.0) > 30 and all(circ(off, k) > 30 for k in keep[ri]):
                keep[ri].append(off)
        out = []
        for ri, r in enumerate(self.rotors):
            moves = list(keep[ri])
            if (
                moves and self.st.wells_keep_grid
            ):  # a well not seen on the probed parents stays reachable
                per = r.period
                moves += [g for g in r.moves
                          if all(min((g - k) % per, (k - g) % per) > 30 for k in keep[ri])]  # fmt: skip
            out.append(r.with_moves(tuple(moves)) if moves else r)
        self.rotors = out

    def _relax_into(self, items: list[tuple[str, np.ndarray, str]], scratch: MinimaPool) -> None:
        """Relax probe items: charged to the budget, accepted minima go to both pools."""
        new = self._relax(items, scratch, self.restraints)
        for mid in new:
            m = scratch.minima[mid]
            gid = self.pool.add(m.energy, m.coords, m.label, m.origin, m.cell)
            if gid is not None:
                self.cells[gid] = m.cell

    def _round(self) -> bool:
        st = self.st
        share = {"dg": st.share_dg, "single": st.share_single, "reembed": st.share_reembed}
        if (
            st.share_lowmode > 0
            and self.calc.info.supports(CAP_HESSIAN)
            and self.rate["single"] < st.lowmode_gate
        ):
            share["single"] = max(0.0, share["single"] - st.share_lowmode)
            share["lowmode"] = st.share_lowmode
        norm = sum(share.values())
        batch = min(st.batch, self._left())
        items: list[tuple[str, np.ndarray, str]] = []
        for arm, s in share.items():
            if s <= 0:
                continue
            q = max(1, int(batch * s / norm))
            if arm == "dg":
                items += self._dg_more(q)
            elif arm == "reembed":
                items += self._reembed(q)
            else:
                items += self._moves(arm, q)
        if not items:
            return False
        self.rng.shuffle(items)  # budget truncation must not favour one arm
        items = items[: self._left()]
        before = len(self.pool)
        self._relax(items, self.pool, self.restraints)
        lo = self.pool.lowest()
        new = [
            m
            for m in self.pool.minima[before:]
            if (m.energy - lo) * HARTREE_TO_KCAL <= st.window_kcal
        ]
        low = sum((m.energy - lo) * HARTREE_TO_KCAL <= 3.0 for m in self.pool.minima[before:])
        self.history.append((self.nopt, int(low), lo))
        for arm in share:
            n = sum(it[2].split(":")[0] == arm for it in items)
            f = sum(m.origin.split(":")[0] == arm for m in new)
            self.spent[arm] += n
            self.found[arm] += f
            if arm == "single" and n:
                self.rate["single"] = 0.5 * self.rate["single"] + 0.5 * f / n
        return True

    def _saturated(self) -> bool:
        """Return whether an adaptive search has stopped finding low-energy minima."""
        st = self.st
        if not st.adaptive or self.nopt < st.min_budget or not self.history:
            return False
        span = max(st.saturation_window, int(0.25 * self.nopt))
        start = self.nopt - span
        before = [h for h in self.history if h[0] <= start]
        if not before:
            return False
        found = sum(h[1] for h in self.history if h[0] > start)
        gain = (before[-1][2] - self.pool.lowest()) * HARTREE_TO_KCAL
        return found <= st.saturation_rate * span and gain < 0.1

    def _release(self) -> MinimaPool:
        """Re-relax the minima near the lowest with only reaction-pair restraints."""
        if not self.st.release:
            self.release_status = "off"
            return self.pool
        if self.free_restraints == self.restraints:
            self.release_status = "not_needed"
            return self.pool
        near = self.pool.within(self.st.window_kcal + 2.0)
        out = MinimaPool(self.id)
        items = [(f"release/m{m.id:05d}", m.coords, f"release:{m.id}") for m in near]
        self._relax(items, out, self.free_restraints, free=True)
        self.released = len(items)
        if len(out):
            self.release_status = "done"
            return out
        # every release failed: publish the restrained minima, flagged in the summary
        self.release_status = "failed"
        return self.pool

    def run(self) -> tuple[MinimaPool, dict[str, Any]]:
        st = self.st
        ntar = max(1, len(self.targets))
        first = st.starts or (max(2, math.ceil(60 / ntar)) if self.spec is not None else 60)
        self._relax(self._dg([(t, first) for t in self.targets], "dg0"), self.pool, self.restraints)
        for m in self.pool.minima:
            self.yields[m.label] += 1
        if self.spec is not None:
            good = [t for t in self.targets if self.yields.get(t.id)] or list(self.targets)
            more = max(2, math.ceil(120 / max(1, len(good))))
            self._relax(self._dg([(t, more) for t in good], "dg1"), self.pool, self.restraints)
        tries = 0
        while not len(self.pool) and self._left() > 0 and tries < 3:
            tries += 1  # nothing accepted yet (e.g. every embedding failed): more global starts
            plan = [(t, max(first, 8) * 2**tries) for t in self.targets]
            self._relax(self._dg(plan, f"dgr{tries}"), self.pool, self.restraints)
        if not len(self.pool):
            self.stop = "no_minima"
            return self.pool, self._stats()
        if self.rotors and st.probes > 0:
            self._probe_wells()
        self.stop = "budget"
        while self._left() > 0:
            self._poll()
            if not self._round():
                self.stop = "no_moves"
                break
            if self._saturated():
                self.stop = "saturated"
                break
        return self._release(), self._stats()

    def _stats(self) -> dict[str, Any]:
        return {
            "relaxations": self.nopt,
            "adaptive": self.st.adaptive,
            "stop": self.stop,
            "release": self.release_status,
            "release_relaxations": self.released,
            "hessians": self.hessians,
            "rotors": len(self.rotors),
            "targets_searched": [t.id for t in self.targets],
            "unique_minima": len(self.pool),
            "spent": dict(self.spent),
            "found_in_window": dict(self.found),
            "failures": dict(sorted(self.fail.items())),
        }


def explore(
    graph: TypedGraph,
    spec: CoordinationSpec | None,
    reference: np.ndarray,
    shape: str | None,
    settings: ExploreSettings,
    calculator: Calculator,
    parallel_map: ParallelMap = map,  # type: ignore[assignment]
    should_cancel: Callable[[], bool] | None = None,
) -> search.SearchRun:
    """Run the iterative search and return the published minima as a ``SearchRun``.

    Published records are the unique minima within ``settings.window_kcal`` of the lowest
    (after release), lowest first; each record's ``target`` is the coordination class it
    realises (``t00`` without a metal, ``unclassified`` for a valid structure that fits no
    ideal polyhedron) and ``start`` is its unique minimum id.
    """
    if settings.budget < 1:
        raise search.SearchError(f"budget must be positive, got {settings.budget!r}")
    ex = _Explorer(graph, spec, np.asarray(reference, dtype=float), shape, settings, calculator,
                   parallel_map, should_cancel)  # fmt: skip
    final, stats = ex.run()
    rows = final.within(settings.window_kcal)
    records = tuple(
        search.SearchRecord(m.label, m.id, False, m.energy, True, (), (), 0.0, m.coords, m.label)
        for m in rows
    )
    per = Counter(m.label for m in rows)
    targets = tuple(replace(t, generated=0, relaxed=0, passed=per.get(t.id, 0), realized=per.get(t.id, 0))
                    for t in ex.targets)  # fmt: skip
    totals = {
        "targets": len(targets),
        "generated": ex.nopt + ex.released,
        "relaxed": ex.nopt + ex.released - ex.fail["not_converged"],
        "passed": len(records),
    }
    return search.SearchRun(
        targets, records, records, tuple(settings.fragment_charges), totals, stats
    )
