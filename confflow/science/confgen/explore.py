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
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal

import numpy as np

from confflow.science.confgen import dg_seed, search
from confflow.science.confgen.calculator import (
    CAP_HESSIAN,
    Calculator,
    DistanceRestraint,
    RelaxRequest,
)
from confflow.science.confgen.coordination import enumeration, perception, shapes
from confflow.science.confgen.graph import INPUT_SHAPE, CoordinationSpec, TypedGraph
from confflow.science.confgen.identity import (
    HARTREE_TO_KCAL,
    Identity,
    MinimaPool,
    Minimum,
    typed_mol,
)
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
#: unit-vector RMSD above which a donor arrangement counts as chiral (mirror not superimposable)
_HAND_TOL = 0.1
#: numerical tie tolerance for comparing proper and mirror fits of one candidate
_HAND_FIT_EPS = 1.0e-8


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
    embed_timeout: int = 120  # per start, as the workflow default embed_timeout_seconds
    #: local re-embedding limit (s): measured successes take <= 4 s (Zr7, TS1, Pt), failures run
    #: until the limit, so a long one only stalls the round (the smaller of the two applies)
    reembed_timeout: int = 10
    fragment_charges: tuple[tuple[int, int], ...] = ()
    bond_scale: float = 1.25
    #: Soft wall limit, checked between bounded backend calls.
    time_limit_seconds: float | None = None
    relaxation_batch: int = 4


# ----------------------------------------------------------------------------- worker tasks
def _dg_task(args: tuple[Any, ...]) -> tuple[str, list[np.ndarray], str | None, tuple[int, ...]]:
    graph, spec, ref, shape, target, count, seed, st, window = args
    settings = search.SearchSettings(count, seed, st.small_ring_torsions, st.embed_timeout,
                                     st.fragment_charges, st.bond_scale, window)  # fmt: skip
    try:
        found = search.generate_starts(graph, spec, ref, shape, [target], settings)
    except search.SearchError as exc:
        return target.id, [], str(exc), ()
    err = found.failures[0][1] if found.failures else None
    return target.id, [s.coords for s in found.starts], err, tuple(found.stereo_centers)


def _fit_rmsd(p: np.ndarray, q: np.ndarray, proper: bool) -> float:
    """RMSD of unit-vector rows ``p`` onto ``q`` under the best rotation (or rotation-reflection)."""
    u, s, vt = np.linalg.svd(p.T @ q)
    sign = np.ones(3)
    if (np.linalg.det(u @ vt) < 0) == proper:  # force det +1 (proper) or -1 (improper)
        sign[2] = -1.0
    best = float(np.sum(s * sign))
    return math.sqrt(max(2.0 * len(p) - 2.0 * best, 0.0) / len(p))


def _reembed_limit(st: ExploreSettings) -> int:
    """Return the local re-embedding timeout: the smaller positive limit, 0 for none."""
    limits = [t for t in (st.reembed_timeout, st.embed_timeout) if t > 0]
    return min(limits) if limits else 0


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
        # Tight distance bounds alone cannot distinguish a structure from its mirror image.
        # Pinning the non-free atoms to their coordinates keeps the core's hand and avoids
        # reversing a rigid core around stereocentres in a free ligand region.
        params.SetCoordMap({i: Point3D(*map(float, xyz[i])) for i in fixed})
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
                 should_cancel: Callable[[], bool] | None,
                 checkpoint: Callable[[search.SearchRun], None] | None = None) -> None:  # fmt: skip
        self.graph, self.spec, self.ref, self.shape, self.st = graph, spec, reference, shape, st
        self.calc, self.pmap, self.should_cancel = calc, pmap, should_cancel
        self.checkpoint = checkpoint
        self.started = time.monotonic()
        self.relax_wall = 0.0
        self.audit_evidence: list[dict[str, Any]] = []
        self.rng = random.Random(f"explore-{st.seed}")
        self.metal = graph.metal_center
        if shape == INPUT_SHAPE and st.arrangement != "keep":
            raise search.SearchError("shape 'input' supports arrangement 'keep' only")
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
        self.arr_vec: np.ndarray = np.empty((0, 3))  # reference donor unit vectors (metal->donor)
        self.arr_chiral = False  # the reference arrangement has no mirror-superimposable relabel
        self.unclassified_keep = False  # keep requested, but the input fits no class
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
        self.marks: dict[str, set[int]] = {}
        self.pending_moves: dict[str, list[tuple[str, np.ndarray, str]]] = {}
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
        assert self.spec is not None
        full, els = self.id.full, self.elements
        heavy = [i for i, e in enumerate(els) if e != "H"]
        pos = {a: k for k, a in enumerate(heavy)}
        hcount = [1 + sum(els[k] == "H" for k in full[a]) for a in heavy]
        mol = typed_mol(
            els, full, heavy, hcount
        )  # edge kinds kept: donors of different roles never swap
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
        if self.spec is None or self.shape is None or self.shape == INPUT_SHAPE:
            return  # the input shape has one target: no rotation group to reduce by
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

    def _donor_vectors(self, xyz: np.ndarray) -> np.ndarray:
        assert self.metal is not None
        v = np.array([xyz[d] - xyz[self.metal] for d in self.donors])
        return v / np.linalg.norm(v, axis=1)[:, None]

    def _keep_arrangement(self) -> None:
        """Search and accept only the input structure's donor arrangement.

        Applies only when the input itself realises a coordination class of the declared
        shape; a strongly distorted input (no class) keeps every class in scope.
        """
        try:
            self.arr_perms = [list(pm) for pm in self._donor_perms()]
        except Exception:
            self.arr_perms = [list(range(len(self.donors)))]
        if self.shape == INPUT_SHAPE:  # the input is the arrangement: no class to classify
            self._set_arrangement()
            return
        rec = search.audit_structure(self.ref, self.graph, self.spec, self.ref, self.shape, (), (),
                                     True, self.st.bond_scale, allowed=self.allowed)  # fmt: skip
        if "coordination_class" in rec.failed_checks or rec.actual_class not in self.tid:
            self.unclassified_keep = True
            return  # the input fits no ideal polyhedron: it has no arrangement to keep
        want = self.tid[rec.actual_class]
        self.targets = tuple(t for t in self.targets if self.tid[t.command] == want)
        self._set_arrangement()

    def _set_arrangement(self) -> None:
        """Store the reference angles, donor vectors and whether the arrangement is chiral."""
        self.arr_ref = self._angles(self.ref)
        vec = self._donor_vectors(self.ref)
        self.arr_vec = vec
        # chiral unless some relabelling of the reference is mirror-superimposable on itself
        self.arr_chiral = min(_fit_rmsd(vec[pm], vec, proper=False)
                              for pm in self.arr_perms) > _HAND_TOL  # fmt: skip

    def _same_arrangement(self, xyz: np.ndarray) -> bool:
        """Return whether some admissible relabelling keeps the donor angles and the hand.

        The angle test alone is mirror-invariant; for a chiral reference the donor vectors must
        also fit by a proper rotation no worse than by a reflection.
        """
        if self.arr_ref is None:
            return True
        a = self._angles(xyz)
        vec = self._donor_vectors(xyz) if self.arr_chiral else None
        for pm in self.arr_perms:
            if float(np.abs(a[np.ix_(pm, pm)] - self.arr_ref).max()) > _ARR_TOL:
                continue
            if vec is None:
                return True
            proper = _fit_rmsd(vec[pm], self.arr_vec, proper=True)
            mirror = _fit_rmsd(vec[pm], self.arr_vec, proper=False)
            if proper <= mirror + _HAND_FIT_EPS:
                return True
        return False

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
        left = max(0, self.st.budget - self.nopt)
        if self.st.time_limit_seconds is not None:
            remaining = self.st.time_limit_seconds - (time.monotonic() - self.started)
            estimate = self.relax_wall / self.nopt if self.nopt else 0.0
            if remaining <= max(0.05, estimate):
                self.stop = "time_limit"
                return 0
            if estimate > 0:
                slots = max(0, math.floor(remaining / estimate) - 1)
                left = min(left, slots)
                if not slots:
                    self.stop = "time_limit"
        return left

    def _checkpoint(self, pool: MinimaPool | None = None, free: bool = False) -> None:
        # Failed release attempts must not erase a useful restrained recovery artifact.
        if free and pool is not None and not len(pool):
            pool, free = self.pool, False
        if self.checkpoint is not None:
            stats = self._stats()
            stats.update(
                checkpoint=True,
                checkpoint_stage="released" if free else "restrained",
                release="pending" if self.st.release else "off",
            )
            self.checkpoint(self._result(self.pool if pool is None else pool, stats))

    def _rel(self, m: Minimum) -> float:
        return (m.energy - self.pool.lowest()) * HARTREE_TO_KCAL

    def _audit(
        self,
        xyz: np.ndarray,
        conv: bool,
        free: bool,
        audit_key: str | None = None,
        origin: str | None = None,
    ) -> tuple[str | None, tuple[str, ...]]:
        """Return (label, ()) for an accepted structure, else (None, failed checks)."""
        rec = search.audit_structure(xyz, self.graph, self.spec, self.ref, self.shape, (), self.centers,
                                     conv, self.st.bond_scale, allowed=self.allowed if self.shape else None)  # fmt: skip
        if rec.coordination_contacts:
            self.audit_evidence.append(
                {
                    "key": audit_key,
                    "origin": origin,
                    "free": free,
                    "failed_checks": list(rec.failed_checks),
                    "contacts": [asdict(c) for c in rec.coordination_contacts],
                }
            )
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
        if (
            bad == ["coordination_class"]
            and self.metal is not None
            and self.shape != INPUT_SHAPE
            and not any(c.changed for c in rec.coordination_contacts)
        ):
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
        ids: list[int] = []
        items = items[: self._left()] if not free else items
        start = 0
        while start < len(items):
            self._poll()
            if not self._left() and (not free or self.stop == "time_limit"):
                break
            size = self.st.relaxation_batch
            if not free:
                size = min(size, self._left())
            elif self.st.time_limit_seconds is not None and self.nopt:
                remaining = self.st.time_limit_seconds - (time.monotonic() - self.started)
                estimate = self.relax_wall / self.nopt
                if estimate > 0:
                    size = min(size, max(0, math.floor(remaining / estimate) - 1))
            if size < 1:
                self.stop = "time_limit"
                break
            batch = items[start : start + size]
            start += len(batch)
            reqs = [RelaxRequest(key, x, restraints) for key, x, _ in batch]
            began = time.monotonic()
            outs = self.calc.relax_many(reqs)
            if len(outs) != len(reqs):
                raise search.SearchError("calculator returned an incomplete relaxation batch")
            if not free:
                self.relax_wall += time.monotonic() - began
                self.nopt += len(batch)
            else:
                self.released += len(batch)
            rows = []
            for (key, _, origin), out in zip(batch, outs):
                if out.coords is None or out.energy_eh is None:
                    self.fail["not_converged"] += 1
                    continue
                label, bad = self._audit(out.coords, out.converged, free, key, origin)
                if label is None:
                    self.fail.update(bad)
                    continue
                cell = label + "#" + self.topo.ring_signature(out.coords)
                rows.append((float(out.energy_eh), out.coords, label, origin, cell))
            ids.extend(mid for mid in pool.add_many(rows, self.pmap) if mid is not None)
            if pool is not self.pool and not free:
                self.pool.add_many(rows, self.pmap)
            self._checkpoint(pool if free else self.pool, free)
        return ids

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
                c = m.cell
                if c not in best or m.energy < best[c].energy:
                    best[c] = m
            extra = [m for m in cand if best.get(m.cell) is m and m not in pick]
            if pick:
                used.update(m.id for m in extra[: max(1, k // 6)])
            else:
                pick = extra[:k]
        used.update(m.id for m in pick)
        return pick

    # -- generators ----------------------------------------------------------------------------
    def _dg(self, plan: list[tuple[Any, int]], tag: str) -> list[tuple[str, np.ndarray, str]]:
        self._poll()
        remaining = self._left()
        bounded = []
        for target, count in plan:
            count = min(count, remaining)
            if count:
                bounded.append((target, count))
                remaining -= count
        plan = bounded
        self.dg_round += 1
        # generate_starts derives RDKit seeds as seed * 1000 + small offsets: keep it 32-bit
        base = self.st.seed * 100003 + 7919 * self.dg_round
        # one task per start, each a slice of one request (seeds as for the whole request), so
        # a single target fills every worker and one slow start delays no other start
        chunks = [(k, t, c, (i, i + 1)) for k, (t, c) in enumerate(plan) for i in range(c)]
        tasks = [(self.graph, self.spec, self.ref, self.shape, t, c, (base + 31 * k) % _SEED_MOD,
                  self.st, w) for k, t, c, w in chunks]  # fmt: skip
        items = []
        results = self.pmap(_dg_task, tasks)
        for (_, _, _, (lo, hi)), (tid, coords, err, centers) in zip(chunks, results):
            if centers and not self.centers:
                self.centers = centers
            if err:
                self.fail["dg_embed"] += 1
            if len(coords) < hi - lo:  # timed out or unembeddable starts are skipped silently
                self.fail["dg_no_start"] += hi - lo - len(coords)
            items += [(f"{tag}/{tid}_{lo:02d}", xyz, f"dg:{tid}") for xyz in coords]
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
        items = self.pending_moves.pop(arm, [])
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
        self.pending_moves[arm] = items[quota:]
        return items[:quota]

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
        tasks: list[tuple[Any, ...]] = []
        names: list[tuple[str, str]] = []
        for m in ps:
            for k, s in enumerate(self._free_sets(min(12, quota - len(tasks)))):
                tasks.append((self.elements, self.bonds, self.metal, m.coords, frozenset(s),
                              self.re_pairs, self.rng.randrange(1, 2**30), _reembed_limit(self.st)))  # fmt: skip
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
        if len(good) > quota:
            good = self.rng.sample(good, quota)
        if not good:
            return []
        counts = [quota // len(good) + (i < quota % len(good)) for i in range(len(good))]
        return self._dg(list(zip(good, counts)), f"dgm{self.dg_round + 1}")

    # -- phases ----------------------------------------------------------------------------------
    def _probe_wells(self, quota: int) -> None:
        best: dict[str, Minimum] = {}
        for m in self.pool.minima:
            c = m.cell
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
        self._relax_into(items[:quota], scratch)
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
        self._relax(items, scratch, self.restraints)

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
        batch = min(st.batch, max(4, st.relaxation_batch), self._left())
        remaining = batch
        items: list[tuple[str, np.ndarray, str]] = []
        for arm, s in share.items():
            if s <= 0 or remaining <= 0:
                continue
            q = min(remaining, max(1, int(batch * s / norm)))
            remaining -= q
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
        before_nopt = self.nopt
        self._relax(items, self.pool, self.restraints)
        items = items[: self.nopt - before_nopt]
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
        if not self._left() and self.stop == "time_limit":
            self.release_status = "deferred"
            return self.pool
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
        if len(out):
            self.release_status = "partial" if self.released < len(items) else "done"
            return out
        # every release failed: publish the restrained minima, flagged in the summary
        self.release_status = "failed"
        return self.pool

    def run(self) -> tuple[MinimaPool, dict[str, Any]]:
        st = self.st
        # One small bootstrap batch; distribute it across targets before repeats.
        initial = min(self._left(), max(1, min(st.relaxation_batch, st.budget // 4)))
        targets = list(self.targets)
        if len(targets) > initial:
            targets = self.rng.sample(targets, initial)
        counts = [0] * len(targets)
        for i in range(initial):
            if not targets:
                break
            k = i % len(targets)
            if st.starts is None or counts[k] < st.starts:
                counts[k] += 1
        plan = [(target, count) for target, count in zip(targets, counts) if count]
        self._relax(self._dg(plan, "dg0"), self.pool, self.restraints)
        for m in self.pool.minima:
            self.yields[m.label] += 1
        tries = 0
        while not len(self.pool) and self._left() > 0 and tries < 3:
            tries += 1
            self._relax(
                self._dg_more(min(st.relaxation_batch, self._left())), self.pool, self.restraints
            )
        if not len(self.pool):
            if self.stop != "time_limit":
                self.stop = "no_minima"
            return self.pool, self._stats()
        if self.rotors and st.probes > 0:
            self._probe_wells(min(st.relaxation_batch, max(0, self._left() // 4)))
        while self._left() > 0:
            self._poll()
            if not self._round():
                self.stop = "no_moves"
                break
            if self._saturated():
                self.stop = "saturated"
                break
        return self._release(), self._stats()

    def _scope(self) -> tuple[str | None, str | None]:
        """Return (template, arrangement scope) as reported in the stats."""
        if self.metal is None or self.spec is None:
            return None, None
        template = self.shape  # the input template is named by INPUT_SHAPE itself
        if self.st.arrangement == "enumerate":
            return template, "all_classes"
        if self.arr_ref is not None:
            return template, "input_arrangement"
        if self.unclassified_keep:
            return template, "all_classes_input_unclassified"
        return template, None

    def _stats(self) -> dict[str, Any]:
        template, scope = self._scope()
        return {
            "template": template,
            "arrangement_scope": scope,
            "relaxations": self.nopt,
            "adaptive": self.st.adaptive,
            "stop": self.stop,
            "release": self.release_status,
            "release_relaxations": self.released,
            "hessians": self.hessians,
            "rotors": len(self.rotors),
            "targets_searched": [t.id for t in self.targets],
            "input_shape_fit": (
                perception.shape_fit(
                    self.ref, self.metal, self.donors, self.spec.site_ids, self.shape
                )
                if self.metal is not None
                and self.donors
                and self.shape
                and self.spec is not None
                and self.shape != INPUT_SHAPE
                else None
            ),  # fmt: skip
            "unique_minima": len(self.pool),
            "spent": dict(self.spent),
            "found_in_window": dict(self.found),
            "failures": dict(sorted(self.fail.items())),
            "coordination_evidence": list(self.audit_evidence),
        }

    def _result(self, final: MinimaPool, stats: dict[str, Any]) -> search.SearchRun:
        rows = final.within(self.st.window_kcal)
        records = tuple(
            search.SearchRecord(
                m.label,
                m.id,
                False,
                m.energy,
                True,
                (),
                (),
                0.0,
                m.coords,
                m.label,
                coordination_contacts=search.audit_structure(
                    m.coords,
                    self.graph,
                    self.spec,
                    self.ref,
                    self.shape,
                    (),
                    self.centers,
                    True,
                    self.st.bond_scale,
                    allowed=self.allowed if self.shape else None,
                ).coordination_contacts,
            )
            for m in rows
        )
        per = Counter(m.label for m in rows)
        targets = tuple(replace(t, generated=0, relaxed=0, passed=per.get(t.id, 0), realized=per.get(t.id, 0))
                        for t in self.targets)  # fmt: skip
        totals = {
            "targets": len(targets),
            "generated": self.nopt + self.released,
            "relaxed": self.nopt + self.released - self.fail["not_converged"],
            "passed": len(records),
        }
        return search.SearchRun(
            targets, records, records, tuple(self.st.fragment_charges), totals, stats
        )


def explore(
    graph: TypedGraph,
    spec: CoordinationSpec | None,
    reference: np.ndarray,
    shape: str | None,
    settings: ExploreSettings,
    calculator: Calculator,
    parallel_map: ParallelMap = map,  # type: ignore[assignment]
    should_cancel: Callable[[], bool] | None = None,
    checkpoint: Callable[[search.SearchRun], None] | None = None,
) -> search.SearchRun:
    """Run the iterative search and return the published minima as a ``SearchRun``.

    ``checkpoint`` receives audited, windowed snapshots after each completed backend batch.
    Snapshot stats distinguish restrained and released coordinates; these snapshots are
    recovery artifacts, not resumable random-state snapshots. A running backend call may
    exceed the soft time limit; execution owns hard cancellation.

    Published records are the unique minima within ``settings.window_kcal`` of the lowest
    (after release), lowest first; each record's ``target`` is the coordination class it
    realises (``t00`` without a metal, ``unclassified`` for a valid structure that fits no
    ideal polyhedron) and ``start`` is its unique minimum id.
    """
    if settings.budget < 1:
        raise search.SearchError(f"budget must be positive, got {settings.budget!r}")
    if settings.batch < 1 or settings.relaxation_batch < 1:
        raise search.SearchError("batch sizes must be positive")
    if settings.time_limit_seconds is not None and (
        not math.isfinite(settings.time_limit_seconds) or settings.time_limit_seconds <= 0
    ):
        raise search.SearchError("time_limit_seconds must be positive")
    if settings.reembed_timeout < 0:
        raise search.SearchError(
            f"reembed_timeout must be non-negative, got {settings.reembed_timeout!r}"
        )
    ex = _Explorer(graph, spec, np.asarray(reference, dtype=float), shape, settings, calculator,
                   parallel_map, should_cancel, checkpoint)  # fmt: skip
    final, stats = ex.run()
    return ex._result(final, stats)
