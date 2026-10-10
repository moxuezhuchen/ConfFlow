"""DG search pipeline: starts, restrained relaxation driver, and audit."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

import numpy as np

from confflow.science.bonding import covalent_radius
from confflow.science.confgen import coordination
from confflow.science.confgen.dg_seed import DGSeedError, DGSeedSettings, generate_dg_seeds
from confflow.science.confgen.graph import CoordinationSpec, TypedGraph
from confflow.science.data import get_atomic_number

__all__ = (
    "CHECKS AuditRecord RelaxOutcome SearchCancelled SearchError SearchRecord SearchRun "
    "SearchSettings SearchStart SearchStarts SearchTarget audit_structure "
    "distance_restraints generate_starts plan_targets run_search"
).split()

CHECKS = tuple(
    "converged topology coordination_class stereo reaction_distance metal_donor_distance "
    "donor_orientation contacts".split()
)
_SKIPPED_FREE = ("coordination_class", "metal_donor_distance", "donor_orientation")
_R_TOL, _MD_TOL, _C_SCALE, _D_TOL = 0.02, 0.03, 0.70, 30.0


class SearchError(ValueError):
    pass


class SearchCancelled(SearchError):
    pass


@dataclass(frozen=True, slots=True)
class SearchSettings:
    count: int = 8
    seed: int = 1
    small_ring_torsions: Literal["both", "on", "off"] = "both"
    embed_timeout: int = 120
    fragment_charges: tuple[tuple[int, int], ...] = ()
    bond_scale: float = 1.25


@dataclass(frozen=True, slots=True)
class SearchTarget:
    id: str
    placement: tuple[int, ...] = ()
    trans_pairs: tuple[tuple[str, str], ...] = ()
    generated: int = 0
    relaxed: int = 0
    passed: int = 0
    error: str | None = None
    command: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "placement": [int(v) for v in self.placement],
            "generated": int(self.generated),
            "trans_pairs": [[a, b] for a, b in self.trans_pairs],
            "relaxed": int(self.relaxed),
            "passed": int(self.passed),
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class SearchStart:
    target: str
    index: int
    coords: np.ndarray
    small_ring_torsions: bool


@dataclass(frozen=True, slots=True)
class SearchStarts:
    starts: tuple[SearchStart, ...] = ()
    failures: tuple[tuple[str, str], ...] = ()
    fragment_charges: tuple[tuple[int, int], ...] = ()
    stereo_centers: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class AuditRecord:
    failed_checks: tuple[str, ...] = ()
    skipped_checks: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RelaxOutcome:
    coords: np.ndarray | None = None
    energy_eh: float | None = None
    converged: bool = False
    wall_s: float = 0.0
    detail: str = ""


@dataclass(frozen=True, slots=True)
class SearchRecord:
    target: str
    start: int
    small_ring_torsions: bool
    energy_eh: float | None
    passed: bool
    failed_checks: tuple[str, ...] = ()
    skipped_checks: tuple[str, ...] = ()
    wall_s: float = 0.0
    coords: np.ndarray | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "start": int(self.start),
            "energy_eh": self.energy_eh,
            "passed": bool(self.passed),
            "failed_checks": [str(n) for n in self.failed_checks],
            "wall_s": float(self.wall_s),
            "small_ring_torsions": bool(self.small_ring_torsions),
            "skipped_checks": [str(n) for n in self.skipped_checks],
        }


@dataclass(frozen=True, slots=True)
class SearchRun:
    targets: tuple[SearchTarget, ...] = ()
    records: tuple[SearchRecord, ...] = ()
    passing: tuple[SearchRecord, ...] = ()
    fragment_charges: tuple[tuple[int, int], ...] = ()
    totals: dict[str, int] | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "targets": [t.to_dict() for t in self.targets],
            "structures": [r.to_dict() for r in self.records],
            "fragment_charges": [
                {"atom": int(a) + 1, "charge": int(q)} for a, q in self.fragment_charges
            ],
            "totals": dict(self.totals or {}),
        }


def _check_settings(settings: SearchSettings) -> None:
    count = settings.count
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise SearchError(f"count must be a positive int, got {count!r}")
    if isinstance(settings.seed, bool) or not isinstance(settings.seed, int):
        raise SearchError(f"seed must be an int, got {settings.seed!r}")
    if settings.small_ring_torsions not in ("both", "on", "off"):
        raise SearchError(f"bad small_ring_torsions {settings.small_ring_torsions!r}")
    timeout = settings.embed_timeout
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 0:
        raise SearchError(f"embed_timeout must be a non-negative int, got {timeout!r}")
    scale = settings.bond_scale
    if isinstance(scale, bool) or not np.isfinite(scale) or scale <= 0:
        raise SearchError(f"bond_scale must be positive, got {scale!r}")


def _poll(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel is not None and should_cancel():
        raise SearchCancelled("search cancelled")


def plan_targets(
    graph: TypedGraph, spec: CoordinationSpec | None, shape: str | None
) -> tuple[SearchTarget, ...]:
    if spec is None:
        return (SearchTarget(id="t00"),)
    if not shape:
        raise SearchError("a coordination shape is required with a coordination spec")
    try:
        pipe = coordination.enumeration.enumerate_targets(spec, shape)
    except ValueError as exc:
        raise SearchError(str(exc)) from exc
    site_ids = [site.id for site in spec.binding_sites]
    trans = coordination.shapes.get_shape(shape).trans_pairs
    group = coordination.shapes.proper_rotation_group(shape)
    targets = []
    for pos, cls in enumerate(pipe["shape_classes"]):
        placement = tuple(int(v) for v in cls.representative)
        at = {v: s for s, v in enumerate(placement)}
        labels = [(site_ids[at[f]], site_ids[at[s]]) for f, s in map(sorted, trans)]
        pairs = sorted((a, b) if a <= b else (b, a) for a, b in labels)
        cmd = coordination.enumeration.canonical_representative(placement, group)
        targets.append(SearchTarget(f"t{pos:02d}", placement, tuple(pairs), command=cmd))
    return tuple(targets)


def generate_starts(
    graph: TypedGraph,
    spec: CoordinationSpec | None,
    reference: np.ndarray,
    shape: str | None,
    targets: Sequence[SearchTarget],
    settings: SearchSettings,
) -> SearchStarts:
    _check_settings(settings)
    count, seed, frag = settings.count, settings.seed, tuple(settings.fragment_charges)
    starts: list[SearchStart] = []
    failures: list[tuple[str, str]] = []
    centers: tuple[int, ...] = ()
    for pos, target in enumerate(targets):
        mode = settings.small_ring_torsions
        n_on = (count + 1) // 2 if mode == "both" else count if mode == "on" else 0
        calls = [(n_on, seed * 1000 + 2 * pos + 1, True)] if n_on else []
        calls += [(count - n_on, seed * 1000 + 2 * pos + 2, False)] if count - n_on else []
        acc: list[tuple[np.ndarray, bool]] = []
        err: str | None = None
        for need, rseed, on in calls:
            setting = DGSeedSettings(
                need, rseed, on, timeout_seconds=settings.embed_timeout, fragment_charges=frag
            )
            try:
                made = generate_dg_seeds(
                    graph, spec, reference, list(target.placement), shape or "", setting
                )
            except DGSeedError as exc:
                if err is None:
                    err = str(exc)
                continue
            if not centers and getattr(made, "stereo_centers", None):
                centers = tuple(int(c) for c in made.stereo_centers)
            acc += [(np.asarray(s, dtype=float), on) for s in made.coords]
        for k, (xyz, on) in enumerate(acc):
            starts.append(SearchStart(target.id, k, xyz, on))
        if err is not None:
            failures.append((target.id, err))
    return SearchStarts(tuple(starts), tuple(failures), frag, centers)


def distance_restraints(
    graph: TypedGraph, spec: CoordinationSpec | None, reference: np.ndarray
) -> tuple[tuple[int, int, float], ...]:
    ref = np.asarray(reference, dtype=float)
    metal = graph.metal_center
    donors = list(spec.donor_indices) if spec is not None else []
    pairs = [(int(a), int(b)) for a, b in graph.reaction_pairs]
    wanted = ([(metal, d) for d in donors] if metal is not None else []) + pairs
    return tuple((i, j, float(np.linalg.norm(ref[i] - ref[j]))) for i, j in wanted)


def _radius(symbol: str) -> float:
    radius = covalent_radius(get_atomic_number(symbol))
    if radius is None:
        raise ValueError(f"element {symbol!r} has no usable covalent radius")
    return float(radius)


def _dist(xyz: np.ndarray, i: int, j: int) -> float:
    return float(np.linalg.norm(xyz[i] - xyz[j]))


def _perceive_bonds(
    xyz: np.ndarray,
    elements: Sequence[str],
    metal: int | None,
    forbidden: Sequence[tuple[int, int]],
    scale: float,
) -> set[tuple[int, int]]:
    radii = [_radius(s) for s in elements]
    skip = {(min(a, b), max(a, b)) for a, b in forbidden}
    bonds: set[tuple[int, int]] = set()
    for i in range(len(elements)):
        if i == metal:
            continue
        for j in range(i + 1, len(elements)):
            if j == metal or (i, j) in skip:
                continue
            if _dist(xyz, i, j) < scale * (radii[i] + radii[j]):
                bonds.add((i, j))
    return bonds


def _adjacency(n: int, bonds: set[tuple[int, int]], metal: int | None) -> list[list[int]]:
    adj: list[list[int]] = [[] for _ in range(n)]
    for i, j in bonds:
        adj[i].append(j)
        adj[j].append(i)
    for i, row in enumerate(adj):
        adj[i] = sorted(k for k in row if k != metal)
    if metal is not None:
        adj[metal] = []
    return adj


def _separation(adj: list[list[int]], src: int) -> list[float]:
    dist = [float("inf")] * len(adj)
    dist[src] = 0.0
    queue = [src]
    while queue:
        node = queue.pop(0)
        for peer in adj[node]:
            if dist[peer] == float("inf"):
                dist[peer] = dist[node] + 1.0
                queue.append(peer)
    return dist


def audit_structure(
    coords: np.ndarray,
    graph: TypedGraph,
    spec: CoordinationSpec | None,
    reference: np.ndarray,
    shape: str | None,
    commanded: Sequence[int] = (),
    stereo_centers: Sequence[int] = (),
    converged: bool = True,
    bond_scale: float = 1.25,
) -> AuditRecord:
    ref = np.asarray(reference, dtype=float)
    new = np.asarray(coords, dtype=float)
    elements = [atom.element for atom in graph.atoms]
    metal = graph.metal_center
    pairs = [(int(a), int(b)) for a, b in graph.reaction_pairs]
    failed = [] if converged else ["converged"]
    ref_bonds = _perceive_bonds(ref, elements, metal, pairs, bond_scale)
    try:
        new_bonds = _perceive_bonds(new, elements, metal, pairs, bond_scale)
    except ValueError:
        new_bonds = set()
        failed.append("topology")
    if "topology" not in failed and new_bonds != ref_bonds:
        failed.append("topology")
    donors: list[int] = []
    if metal is not None:
        if spec is None or shape is None:
            raise SearchError("metal-bearing audit needs a coordination spec and shape")
        donors = list(spec.donor_indices)
        sites = [site.id for site in spec.binding_sites]
        group = coordination.shapes.proper_rotation_group(shape)
        try:
            seen = coordination.perception.perceive_donors(new, metal, donors, sites, shape)
            got = coordination.enumeration.canonical_representative(tuple(seen.best_class), group)
            ok = bool(seen.unambiguous) and got == tuple(commanded)
        except ValueError:
            ok = False
        if not ok:
            failed.append("coordination_class")
    adj = _adjacency(len(elements), ref_bonds, metal)
    stereo = True
    for i in stereo_centers:
        if not 0 <= i < len(elements) or len(adj[i]) != 4:
            continue
        pick = tuple(adj[i][:4])
        before = coordination.realization.signed_volume(ref, i, pick)
        after = coordination.realization.signed_volume(new, i, pick)
        same = (before > 0.0) == (after > 0.0) if before and after else before == after == 0.0
        stereo = stereo and same
    if not stereo:
        failed.append("stereo")
    for i, j in pairs:
        if abs(_dist(new, i, j) - _dist(ref, i, j)) > _R_TOL:
            failed.append("reaction_distance")
            break
    for d in donors:
        if metal is not None and abs(_dist(new, metal, d) - _dist(ref, metal, d)) > _MD_TOL:
            failed.append("metal_donor_distance")
            break
    if metal is not None:
        worst = 0.0
        for d in donors:
            for x in adj[d]:
                v_ref, w_ref = ref[metal] - ref[d], ref[x] - ref[d]
                v_new, w_new = new[metal] - new[d], new[x] - new[d]
                n_ref = float(np.linalg.norm(v_ref) * np.linalg.norm(w_ref))
                n_new = float(np.linalg.norm(v_new) * np.linalg.norm(w_new))
                if n_ref < 1e-12 or n_new < 1e-12:
                    continue
                a_ref = float(
                    np.degrees(np.arccos(np.clip(float(np.dot(v_ref, w_ref)) / n_ref, -1.0, 1.0)))
                )
                a_new = float(
                    np.degrees(np.arccos(np.clip(float(np.dot(v_new, w_new)) / n_new, -1.0, 1.0)))
                )
                worst = max(worst, abs(a_new - a_ref))
        if worst > _D_TOL:
            failed.append("donor_orientation")
    radii, clash = [_radius(s) for s in elements], False
    for i in range(len(elements)):
        if i == metal:
            continue
        sep = _separation(adj, i)
        far = [j for j in range(i + 1, len(elements)) if j != metal and sep[j] > 3.0]
        clash = any(_dist(new, i, j) < _C_SCALE * (radii[i] + radii[j]) for j in far)
        if clash:
            break
    if clash:
        failed.append("contacts")
    skipped = () if metal is not None else _SKIPPED_FREE
    return AuditRecord(tuple(n for n in CHECKS if n in failed), tuple(skipped))


RelaxFn = Callable[
    [tuple[SearchStart, ...], tuple[tuple[int, int, float], ...]], Sequence[RelaxOutcome]
]


def _energy_key(record: SearchRecord) -> tuple[float, str, int]:
    return (float(record.energy_eh or 0.0), record.target, record.start)


def run_search(
    graph: TypedGraph,
    spec: CoordinationSpec | None,
    reference: np.ndarray,
    shape: str | None,
    settings: SearchSettings,
    relax: RelaxFn,
    should_cancel: Callable[[], bool] | None = None,
) -> SearchRun:
    targets = plan_targets(graph, spec, shape)
    _poll(should_cancel)
    found = generate_starts(graph, spec, reference, shape, targets, settings)
    _poll(should_cancel)
    restraints = distance_restraints(graph, spec, reference)
    outcomes = relax(found.starts, restraints)
    if len(outcomes) != len(found.starts):
        raise SearchError(f"relax returned {len(outcomes)} outcomes for {len(found.starts)} starts")
    _poll(should_cancel)
    commanded = {t.id: t.command for t in targets}
    centers, scale = found.stereo_centers, settings.bond_scale
    skipped = () if graph.metal_center is not None else _SKIPPED_FREE
    failures = dict(found.failures)
    tallies = {t.id: [0, 0, 0] for t in targets}
    records: list[SearchRecord] = []
    for start, outcome in zip(found.starts, outcomes):
        new = outcome.coords
        tallies[start.target][0] += 1
        if new is None:
            bad: tuple[str, ...] = ("converged",)
        else:
            tallies[start.target][1] += 1
            cmd = commanded.get(start.target, ())
            bad = audit_structure(
                new, graph, spec, reference, shape, cmd, centers, outcome.converged, scale
            ).failed_checks
        ok = not bad
        if ok:
            tallies[start.target][2] += 1
        target, index, flag = start.target, start.index, start.small_ring_torsions
        energy, wall = outcome.energy_eh, float(outcome.wall_s)
        records.append(
            SearchRecord(target, index, flag, energy, ok, bad, skipped, wall, new if ok else None)
        )
    _poll(should_cancel)
    done = []
    for t in targets:
        g, r, p = tallies[t.id]
        done.append(replace(t, generated=g, relaxed=r, passed=p, error=failures.get(t.id)))
    passing = tuple(sorted((r for r in records if r.passed), key=_energy_key))
    relaxed = sum(t.relaxed for t in done)
    totals = dict(targets=len(done), generated=len(records), passed=len(passing), relaxed=relaxed)
    return SearchRun(tuple(done), tuple(records), passing, tuple(settings.fragment_charges), totals)
