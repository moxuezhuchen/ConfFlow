"""DG search building blocks: coordination targets, DG starts, restraints, and the audit.

The iterative search that uses them lives in ``confflow.science.confgen.explore``; every
relaxation goes through the backend-neutral ``Calculator`` protocol, so this module never
names an engine.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal

import numpy as np

from confflow.science.bonding import covalent_radius
from confflow.science.confgen.calculator import (
    DistanceRestraint,
)
from confflow.science.confgen.calculator import (
    RelaxOutcome as RelaxOutcome,  # re-exported: search.RelaxOutcome predates the protocol
)
from confflow.science.confgen.coordination import enumeration, perception, shapes
from confflow.science.confgen.dg_seed import (
    DETACHED_DISTANCE_FACTOR,
    DGSeedError,
    DGSeedSettings,
    detached_donors_of,
    generate_dg_seeds,
)
from confflow.science.confgen.graph import INPUT_SHAPE, CoordinationSpec, EdgeType, TypedGraph
from confflow.science.confgen.identity import Identity, MinimaPool
from confflow.science.confgen.stereo import (
    DoubleBondState,
    TetrahedralState,
    TorsionState,
    preserves_double_bonds,
    preserves_tetrahedra,
    preserves_torsions,
)
from confflow.science.confgen.stereo import (
    double_bond_states as perceive_double_bond_states,
)
from confflow.science.confgen.stereo import (
    tetrahedral_states as perceive_tetrahedral_states,
)
from confflow.science.data import get_atomic_number

__all__ = (
    "CHECKS AuditRecord CoordinationContact InputBaseline RelaxOutcome SearchCancelled SearchError SearchRecord "
    "SearchRun SearchSettings SearchStart SearchStarts SearchTarget audit_structure "
    "DETACHED_DISTANCE_FACTOR detached_restraints distance_restraints generate_starts "
    "optional_bound_state plan_targets DedupedRecords dedupe_records"
).split()

CHECKS = tuple(
    "converged topology coordination_class stereo double_bond_stereo fixed_state reaction_distance metal_donor_distance "
    "donor_orientation contacts".split()
)
_SKIPPED_FREE = ("coordination_class", "metal_donor_distance", "donor_orientation")
_R_TOL, _MD_TOL, _C_SCALE, _D_TOL = 0.02, 0.03, 0.70, 30.0
#: Topology hysteresis (x covalent-radius sum): a reference bond breaks only beyond _BREAK,
#: a new bond forms only below _FORM; ordinary stretching during relaxation is not a change.
_BREAK, _FORM = 1.45, 1.10
#: New-coordination rule for the input shape (Angstrom / x covalent-radius sum): a non-donor
#: atom counts as newly bound only if it is close to the metal AND closer than in the reference
#: by _NEW_CONTACT_DROP. The drop is relative because small-bite chelates (kappa2-nitrate) put a
#: non-donor atom near the metal already in the input; an absolute cut-off would misread that.
_NEW_CONTACT_SCALE, _NEW_CONTACT_DROP = 1.25, 0.3


def _signed_volume(coords: np.ndarray, center: int, neighbors: Sequence[int]) -> float:
    """Return the signed volume of the tetrahedron around *center*.

    The search audit is the only reader of this stereo-volume helper.
    """
    selected = np.asarray([coords[n] for n in neighbors[:4]], dtype=float)
    mat = np.column_stack(
        [selected[1] - selected[0], selected[2] - selected[0], selected[3] - selected[0]]
    )
    return float(np.linalg.det(mat) / 6.0)


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
    #: embed only starts ``lo <= k < hi`` of the ``count`` (same seeds as the full request)
    window: tuple[int, int] | None = None
    freeze_core: tuple[int, ...] = ()
    #: drop the donor-donor core bounds of every start (see ``DGSeedSettings.free_arrangement``)
    free_arrangement: bool = False


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
    realized: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "placement": [int(v) for v in self.placement],
            "generated": int(self.generated),
            "trans_pairs": [[a, b] for a, b in self.trans_pairs],
            "relaxed": int(self.relaxed),
            "passed": int(self.passed),
            "realized": int(self.realized),
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
class CoordinationContact:
    """Reference-relative near-contact evidence (internal, zero-based atom indices).

    ``changed`` is a conservative audit decision, not a bond-order assignment.
    """

    metal: int
    atom: int
    element: str
    distance: float
    reference_distance: float
    cutoff: float
    neighbors: tuple[int, ...]
    mode: str
    changed: bool


@dataclass(frozen=True, slots=True)
class AuditRecord:
    failed_checks: tuple[str, ...] = ()
    skipped_checks: tuple[str, ...] = ()
    actual_class: tuple[int, ...] | None = None
    coordination_contacts: tuple[CoordinationContact, ...] = ()


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
    actual_target: str | None = None
    coordination_contacts: tuple[CoordinationContact, ...] = ()
    #: donor arrangement group (``a0`` = the input's); ``None`` without a metal
    arrangement: str | None = None
    #: reaction-pair distances (Angstrom) in ``graph.reaction_pairs`` order
    reaction_distances: tuple[float, ...] = ()
    #: largest metal-donor distance over its input distance, and that donor (0-based)
    donor_stretch: float | None = None
    donor_stretch_atom: int | None = None
    #: 1-based ``state_changes`` units this structure flips; ``None`` when not scanned
    state_flips: tuple[int, ...] | None = None
    #: optional donor (0-based atom index) -> bound at its measured distance; ``None`` if unused
    optional_bound: dict[int, bool] | None = None
    #: ``window`` (inside the energy window) or ``state`` (kept by the state window); ``None`` off
    kept_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "target": self.target,
            "start": int(self.start),
            "energy_eh": self.energy_eh,
            "passed": bool(self.passed),
            "coordination_contacts": [asdict(c) for c in self.coordination_contacts],
            "failed_checks": [str(n) for n in self.failed_checks],
            "wall_s": float(self.wall_s),
            "small_ring_torsions": bool(self.small_ring_torsions),
            "skipped_checks": [str(n) for n in self.skipped_checks],
            "actual_target": self.actual_target,
            "arrangement": self.arrangement,
            "reaction_distances": [float(d) for d in self.reaction_distances],
            "donor_stretch": self.donor_stretch,
            "donor_stretch_atom": self.donor_stretch_atom,
            "state_flips": None if self.state_flips is None else [int(u) for u in self.state_flips],
        }
        if self.optional_bound is not None:
            payload["optional_bound"] = {int(k): bool(v) for k, v in self.optional_bound.items()}
        if self.kept_by is not None:
            payload["kept_by"] = self.kept_by
        return payload


@dataclass(frozen=True, slots=True)
class InputBaseline:
    """Independent input optimization evidence, including candidates outside publication windows.

    Coordinate arrays are copied and read-only, so mutable pool representatives and backend
    request buffers cannot rewrite a completed baseline or an earlier checkpoint.
    """

    status: str = "pending"
    search_record: SearchRecord | None = None
    final_record: SearchRecord | None = None
    search_restraints: tuple[DistanceRestraint, ...] = ()
    final_restraints: tuple[DistanceRestraint, ...] = ()
    raw_failed_checks: tuple[str, ...] = ()
    failed_checks: tuple[str, ...] = ()
    release_relaxations: int = 0
    comparable_to_final: bool = False
    search_origin: str = "input/reference"
    final_origin: str | None = None
    disposition: str = "unavailable"
    published: bool = False
    final_pool_id: int | None = None
    lowest_final_delta_kcal: float | None = None

    def __post_init__(self) -> None:
        for name in ("search_record", "final_record"):
            record = getattr(self, name)
            if record is not None and record.coords is not None:
                coords = np.array(record.coords, dtype=float, copy=True)
                coords.setflags(write=False)
                object.__setattr__(self, name, replace(record, coords=coords))

    def to_dict(self) -> dict[str, Any]:
        def record_dict(record: SearchRecord | None) -> dict[str, Any] | None:
            if record is None:
                return None
            return {
                **record.to_dict(),
                "coordinates": record.coords.tolist() if record.coords is not None else None,
            }

        return {
            "status": self.status,
            "search_record": record_dict(self.search_record),
            "final_record": record_dict(self.final_record),
            "search_restraints": [asdict(r) for r in self.search_restraints],
            "final_restraints": [asdict(r) for r in self.final_restraints],
            "raw_failed_checks": list(self.raw_failed_checks),
            "failed_checks": list(self.failed_checks),
            "release_relaxations": self.release_relaxations,
            "comparable_to_final": self.comparable_to_final,
            "search_origin": self.search_origin,
            "final_origin": self.final_origin,
            "disposition": self.disposition,
            "published": self.published,
            "final_pool_id": self.final_pool_id,
            "lowest_final_delta_kcal": self.lowest_final_delta_kcal,
        }


@dataclass(frozen=True, slots=True)
class SearchRun:
    targets: tuple[SearchTarget, ...] = ()
    records: tuple[SearchRecord, ...] = ()
    passing: tuple[SearchRecord, ...] = ()
    fragment_charges: tuple[tuple[int, int], ...] = ()
    totals: dict[str, int] | None = None
    #: Strategy statistics of an iterative search (budget use, failures); ``None`` otherwise.
    stats: dict[str, Any] | None = None

    input_baseline: InputBaseline | None = None

    def summary(self) -> dict[str, Any]:
        extra = {} if self.stats is None else {"search": dict(self.stats)}
        if self.input_baseline is not None:
            extra["input_baseline"] = self.input_baseline.to_dict()
        return {
            **extra,
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
    win = settings.window
    if win is not None and not (len(win) == 2 and 0 <= win[0] < win[1] <= count):
        raise SearchError(f"window must satisfy 0 <= lo < hi <= count, got {win!r}")


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
    if shape == INPUT_SHAPE:
        # the input's own donor arrangement is the only class; no ideal-shape enumeration
        cn = spec.coordination_number
        return (SearchTarget(id="t00", placement=tuple(range(cn)), command=tuple(range(cn))),)
    try:
        pipe = enumeration.enumerate_targets(spec, shape)
    except ValueError as exc:
        raise SearchError(str(exc)) from exc
    site_ids = [site.id for site in spec.binding_sites]
    trans = shapes.get_shape(shape).trans_pairs
    group = shapes.proper_rotation_group(shape)
    targets = []
    for pos, cls in enumerate(pipe["shape_classes"]):
        placement = tuple(int(v) for v in cls.representative)
        at = {v: s for s, v in enumerate(placement)}
        labels = [(site_ids[at[f]], site_ids[at[s]]) for f, s in map(sorted, trans)]
        pairs = sorted((a, b) if a <= b else (b, a) for a, b in labels)
        cmd = enumeration.canonical_representative(placement, group)
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
        lo, hi = settings.window or (0, count)
        first = 0  # index of the call's first start in the full request
        for need, rseed, on in calls:
            a, b = max(lo, first) - first, min(hi, first + need) - first
            first += need
            if a >= b:
                continue
            setting = DGSeedSettings(
                b - a,
                rseed,
                on,
                timeout_seconds=settings.embed_timeout,
                fragment_charges=frag,
                first_start=a,
                freeze_core=tuple(settings.freeze_core),
                free_arrangement=settings.free_arrangement,
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
) -> tuple[DistanceRestraint, ...]:
    ref = np.asarray(reference, dtype=float)
    metal = graph.metal_center
    donors = list(spec.donor_indices) if spec is not None else []
    pairs = [(int(a), int(b)) for a, b in graph.reaction_pairs]
    wanted = ([(metal, d) for d in donors] if metal is not None else []) + pairs
    return tuple(DistanceRestraint(i, j, float(np.linalg.norm(ref[i] - ref[j]))) for i, j in wanted)


def detached_restraints(
    graph: TypedGraph, spec: CoordinationSpec | None, reference: np.ndarray
) -> tuple[DistanceRestraint, ...]:
    """Equality restraints holding each detached optional donor at its detached distance.

    Restrained stage only: the explorer keeps them out of the release (reaction pairs only),
    so a released structure decides its donor state by measurement.
    """
    metal = graph.metal_center
    if metal is None or spec is None:
        return ()
    ref = np.asarray(reference, dtype=float)
    return tuple(
        DistanceRestraint(
            int(metal),
            int(d),
            DETACHED_DISTANCE_FACTOR * float(np.linalg.norm(ref[metal] - ref[d])),
        )
        for d in detached_donors_of(spec)
    )


def optional_bound_state(
    coords: np.ndarray,
    metal: int,
    optional_refs: Mapping[int, float],
    slack: float = 1.3,
) -> dict[int, bool]:
    """Return ``{atom: bound}`` for optional donors, bound iff distance <= slack x reference.

    ``optional_refs`` maps 0-based donor indices to their input metal-donor distances.
    """
    if not np.isfinite(slack) or slack <= 0:
        raise SearchError(f"slack must be positive, got {slack!r}")
    xyz = np.asarray(coords, dtype=float)
    return {
        int(atom): bool(_dist(xyz, metal, int(atom)) <= slack * float(ref))
        for atom, ref in sorted(optional_refs.items())
    }


@dataclass(frozen=True, slots=True)
class DedupedRecords:
    """Cross-state dedupe result: the minima pool, each kept minimum's state index and record."""

    pool: MinimaPool
    owner: dict[int, int]
    kept: dict[int, SearchRecord]


def dedupe_records(
    graph: TypedGraph,
    records: Sequence[tuple[int, SearchRecord]],
    *,
    freeze_core: Sequence[int] = (),
) -> DedupedRecords:
    """Dedupe ``(state index, record)`` pairs of several states into one minima pool.

    Records are added lowest energy first (ties by state index), so a rediscovery keeps the
    lowest copy together with its state index and record.
    """
    pool = MinimaPool(Identity(graph, fixed_atoms=freeze_core))
    owner: dict[int, int] = {}
    kept: dict[int, SearchRecord] = {}
    ordered = sorted(
        ((float(rec.energy_eh or 0.0), index, rec) for index, rec in records),
        key=lambda item: (item[0], item[1]),
    )
    for energy, index, rec in ordered:
        mid = pool.add(energy, rec.coords, rec.target, f"donor_state{index}")
        if mid is not None:
            owner[mid], kept[mid] = index, rec
    return DedupedRecords(pool, owner, kept)


def _radius(symbol: str) -> float:
    radius = covalent_radius(get_atomic_number(symbol))
    if radius is None:
        raise ValueError(f"element {symbol!r} has no usable covalent radius")
    return float(radius)


def _dist(xyz: np.ndarray, i: int, j: int) -> float:
    return float(np.linalg.norm(xyz[i] - xyz[j]))


def _coordination_contacts(
    new: np.ndarray,
    ref: np.ndarray,
    graph: TypedGraph,
    donors: Sequence[int],
    detached: Sequence[int] = (),
) -> tuple[CoordinationContact, ...]:
    """Explain new near contacts using the fixed covalent graph and local geometry.

    The radius screen alone is particularly permissive for alkali metals. A saturated
    carbon or its hydrogen is not an extra donor to an alkali/alkaline-earth center merely
    because it enters that screen. Very short contacts still fail conservatively; unknown
    valence and possible organometallic modes retain the conservative distance decision.
    This is an audit heuristic, not electronic-structure perception.
    """
    metal = graph.metal_center
    if metal is None:
        return ()
    elements = [atom.element for atom in graph.atoms]
    ionic_metal = elements[metal] in {
        "Li",
        "Na",
        "K",
        "Rb",
        "Cs",
        "Fr",
        "Be",
        "Mg",
        "Ca",
        "Sr",
        "Ba",
        "Ra",
    }
    # A detached optional donor is declared: nearing the metal again is not a new contact.
    declared = set(donors) | set(detached) | {metal}
    result = []
    for j, element in enumerate(elements):
        if j in declared:
            continue
        d_new, d_ref = _dist(new, metal, j), _dist(ref, metal, j)
        radius_sum = _radius(elements[metal]) + _radius(element)
        cutoff = _NEW_CONTACT_SCALE * radius_sum
        if not (d_new < cutoff and d_new < d_ref - _NEW_CONTACT_DROP):
            continue
        neighbors = tuple(graph.neighbors(j, EdgeType.COVALENT))
        changed, mode = True, "additional_donor"
        if element == "C":
            edges = [e for e in graph.edges if e.type == EdgeType.COVALENT and j in (e.a, e.b)]
            saturated = len(neighbors) == 4 and all(e.bond_order in (None, 1.0) for e in edges)
            if saturated:
                # A full valence shell cannot acquire a normal sigma bond without a
                # topology change. Short transition-metal contacts can still be agostic.
                limit = (0.70 if ionic_metal else 1.0) * radius_sum
                changed = d_new < limit
                mode = "short_saturated_carbon" if changed else "saturated_carbon_proximity"
            else:
                mode = "possible_sigma_or_pi_carbon"
        elif element == "H":
            if len(neighbors) == 1 and elements[neighbors[0]] == "C":
                carbon = neighbors[0]
                mh, ch = new[metal] - new[j], new[carbon] - new[j]
                cosine = float(np.dot(mh, ch) / max(np.linalg.norm(mh) * np.linalg.norm(ch), 1e-12))
                angle = float(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0))))
                # C-H donation to transition metals needs a short, bent M-H-C
                # arrangement. Ionic metals lack this ordinary agostic binding mode.
                agostic = (
                    not ionic_metal
                    and d_new < min(2.3, 1.15 * radius_sum)
                    and 80.0 <= angle <= 140.0
                )
                changed = d_new < 0.70 * radius_sum or agostic
                mode = (
                    "possible_agostic_hydrogen"
                    if agostic
                    else (
                        "short_hydrogen_contact" if changed else "carbon_bound_hydrogen_proximity"
                    )
                )
            else:
                mode = "possible_hydride_or_other_hydrogen"
        result.append(
            CoordinationContact(metal, j, element, d_new, d_ref, cutoff, neighbors, mode, changed)
        )
    return tuple(result)


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


def _reference_bonds(
    ref: np.ndarray,
    elements: Sequence[str],
    metal: int | None,
    pairs: Sequence[tuple[int, int]],
    scale: float,
    donors: Sequence[int],
) -> set[tuple[int, int]]:
    """Bonds of the reference; two donors of the metal bond only at real bonding distance."""
    bonds = _perceive_bonds(ref, elements, metal, pairs, scale)
    dd = {(min(a, b), max(a, b)) for a in donors for b in donors if a != b}
    radii = [_radius(e) for e in elements]
    return {
        p for p in bonds if p not in dd or _dist(ref, *p) <= _FORM * (radii[p[0]] + radii[p[1]])
    }


def _topology_changed(
    new: np.ndarray,
    elements: Sequence[str],
    metal: int | None,
    pairs: Sequence[tuple[int, int]],
    ref_bonds: set[tuple[int, int]],
    donors: Sequence[int],
) -> bool:
    """Return whether a reference bond broke or a new bond formed (with hysteresis)."""
    try:
        radii = np.array([_radius(e) for e in elements])
    except ValueError:
        return True
    ratio = np.linalg.norm(new[:, None] - new[None], axis=-1) / (radii[:, None] + radii[None])
    if any(ratio[i, j] > _BREAK for i, j in ref_bonds):
        return True
    skip = {(min(a, b), max(a, b)) for a, b in pairs}
    skip |= {(min(a, b), max(a, b)) for a in donors for b in donors if a != b}
    for i, j in np.argwhere(np.triu(ratio < _FORM, 1)):
        p = (int(i), int(j))
        if metal not in p and p not in skip and p not in ref_bonds:
            return True
    return False


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
    *,
    allowed: Collection[tuple[int, ...]] | None = None,
    double_bond_states: Sequence[DoubleBondState] | None = None,
    tetrahedral_states: Sequence[TetrahedralState] | None = None,
    torsion_states: Sequence[TorsionState] = (),
) -> AuditRecord:
    ref = np.asarray(reference, dtype=float)
    new = np.asarray(coords, dtype=float)
    elements = [atom.element for atom in graph.atoms]
    metal = graph.metal_center
    pairs = [(int(a), int(b)) for a, b in graph.reaction_pairs]
    failed = [] if converged else ["converged"]
    donors = list(spec.donor_indices) if spec is not None and metal is not None else []
    ref_bonds = _reference_bonds(ref, elements, metal, pairs, bond_scale, donors)
    if _topology_changed(new, elements, metal, pairs, ref_bonds, donors):
        failed.append("topology")
    actual: tuple[int, ...] | None = None
    coordination_contacts: tuple[CoordinationContact, ...] = ()
    if metal is not None:
        if spec is None or shape is None:
            raise SearchError("metal-bearing audit needs a coordination spec and shape")
        if shape == INPUT_SHAPE:
            # the donor arrangement is checked by the explorer; here only new coordination counts
            actual = tuple(range(spec.coordination_number))
            detached = detached_donors_of(spec)
            coordination_contacts = _coordination_contacts(new, ref, graph, donors, detached)
            if any(contact.changed for contact in coordination_contacts):
                failed.append("coordination_class")
        else:
            sites = [site.id for site in spec.binding_sites]
            group = shapes.proper_rotation_group(shape)
            wanted = {tuple(commanded)} if allowed is None else {tuple(c) for c in allowed}
            try:
                seen = perception.perceive_donors(new, metal, donors, sites, shape)
                got = enumeration.canonical_representative(tuple(seen.best_class), group)
                actual = got if seen.unambiguous else None
            except ValueError:
                actual = None
            if actual is None or actual not in wanted:
                failed.append("coordination_class")
    adj = _adjacency(len(elements), ref_bonds, metal)
    stereo = True
    for i in stereo_centers:
        if not 0 <= i < len(elements) or len(adj[i]) != 4:
            continue
        pick = tuple(adj[i][:4])
        before = _signed_volume(ref, i, pick)
        after = _signed_volume(new, i, pick)
        same = (before > 0.0) == (after > 0.0) if before and after else before == after == 0.0
        stereo = stereo and same
    if tetrahedral_states is None:
        # Standalone audits hold every four-neighbour hand, as the explorer does.
        from confflow.science.confgen.dg_seed import stable_chemistry_molecule

        try:
            molecule = stable_chemistry_molecule(graph, ref)[0]
            tetrahedral_states = perceive_tetrahedral_states(
                molecule, ref, graph.reaction_pairs, metal
            )[0]
        except ValueError:
            tetrahedral_states = ()
    if not stereo or not preserves_tetrahedra(new, tuple(tetrahedral_states)):
        failed.append("stereo")
    if double_bond_states is None:
        # Public standalone audits must protect E/Z too, even without caller-supplied atom tags.
        from confflow.science.confgen.dg_seed import stable_chemistry_molecule

        try:
            molecule = stable_chemistry_molecule(graph, ref)[0]
            double_bond_states = perceive_double_bond_states(molecule, ref)
        except ValueError:
            double_bond_states = ()
            if any(edge.type is EdgeType.COVALENT and edge.bond_order == 2 for edge in graph.edges):
                failed.append("double_bond_stereo")
    if not preserves_double_bonds(new, tuple(double_bond_states)):
        failed.append("double_bond_stereo")
    if not preserves_torsions(new, tuple(torsion_states)):
        failed.append("fixed_state")
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
    return AuditRecord(
        tuple(n for n in CHECKS if n in failed), tuple(skipped), actual, coordination_contacts
    )
