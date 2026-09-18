#!/usr/bin/env python3

"""RMSD deduplication engine — core computation functions.

Split from processor.py. Contains Numba JIT-accelerated RMSD/PMI calculation,
graph-based topology hashing, and representative-based deduplication logic.

Geometry comparisons are only performed through legal graph mappings produced
by :mod:`confflow.blocks.refine.topology`; a fixed-index RMSD is never assumed
to be the minimum over symmetry-equivalent mappings.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

import numpy as np

from ...core.bonding import build_adjacency
from ._compat import (
    load_console_bindings,
    load_hartree_to_kcal,
    load_numba_runtime,
    load_refine_data,
)
from .topology import (
    DEFAULT_MAPPING_NODE_BUDGET,
    Graph,
    MappingBudgetExceeded,
    MappingSearch,
    MappingSearchStats,
    build_graph,
    build_graph_from_atomic_numbers,
    fixed_index_isomorphism,
    get_element_atomic_number,
    graphs_may_be_isomorphic,
)

logger = logging.getLogger("confflow.refine")

# ---------------------------------------------------------------------------
# Dependency imports (with fallback)
# ---------------------------------------------------------------------------

create_progress = load_console_bindings()["create_progress"]
_periodic_symbols, _ = load_refine_data()
PERIODIC_SYMBOLS: Sequence[str] = cast(Sequence[str], _periodic_symbols)
HARTREE_TO_KCALMOL = load_hartree_to_kcal()

# ---------------------------------------------------------------------------
# Numba & constants
# ---------------------------------------------------------------------------

numba = load_numba_runtime("confflow.refine")

BOND_SCALE_FACTOR = 1.2
PMI_TOLERANCE_FACTOR = 0.05
ENERGY_RMSD_SCALE_FACTOR = 1.5

__all__ = [
    "BOND_SCALE_FACTOR",
    "PMI_TOLERANCE_FACTOR",
    "ENERGY_RMSD_SCALE_FACTOR",
    "DEFAULT_MAPPING_NODE_BUDGET",
    "PairVerdict",
    "check_one_against_many",
    "compare_frames",
    "fast_rmsd",
    "get_element_atomic_number",
    "get_pmi",
    "get_principal_axes",
    "get_topology_hash_worker",
    "greedy_permutation_rmsd",
    "kabsch_rmsd",
    "process_topology_group",
]


# ---------------------------------------------------------------------------
# Numba JIT core functions
# ---------------------------------------------------------------------------


@numba.njit(fastmath=True, cache=True)
def get_pmi(coords):
    if coords.shape[0] == 0:
        return np.array([0.0, 0.0, 0.0])
    center = coords.sum(axis=0) / coords.shape[0]
    coords_centered = coords - center
    inertia = np.zeros((3, 3))
    for i in range(coords_centered.shape[0]):
        x, y, z = coords_centered[i]
        inertia[0, 0] += y**2 + z**2
        inertia[1, 1] += x**2 + z**2
        inertia[2, 2] += x**2 + y**2
        inertia[0, 1] -= x * y
        inertia[0, 2] -= x * z
        inertia[1, 2] -= y * z
    inertia[1, 0] = inertia[0, 1]
    inertia[2, 0] = inertia[0, 2]
    inertia[2, 1] = inertia[1, 2]
    return np.sort(np.linalg.eigvalsh(inertia))


@numba.njit(fastmath=True, cache=True)
def get_principal_axes(coords):
    """Return (eigenvalues, eigenvector_matrix) of the inertia tensor.

    Eigenvalues are sorted ascending; columns of *eigvecs* are principal axes.
    Kept as a compatibility helper for the legacy greedy matcher.
    """
    n = coords.shape[0]
    if n == 0:
        return np.zeros(3), np.eye(3)
    center = coords.sum(axis=0) / n
    c = coords - center
    inertia = np.zeros((3, 3))
    for i in range(n):
        x, y, z = c[i]
        inertia[0, 0] += y * y + z * z
        inertia[1, 1] += x * x + z * z
        inertia[2, 2] += x * x + y * y
        inertia[0, 1] -= x * y
        inertia[0, 2] -= x * z
        inertia[1, 2] -= y * z
    inertia[1, 0] = inertia[0, 1]
    inertia[2, 0] = inertia[0, 2]
    inertia[2, 1] = inertia[1, 2]
    eigvals, eigvecs = np.linalg.eigh(inertia)
    return eigvals, eigvecs


@numba.njit(fastmath=True, cache=True)
def fast_rmsd(coords1, coords2):
    """Proper (reflection-free) Kabsch RMSD on N x 3 row-vector coordinates.

    ``coords1`` is the fixed target ``X`` and ``coords2`` is the moving set
    ``Y``.  With ``H = Y.T @ X`` and ``U, S, Vt = svd(H)`` the optimal proper
    rotation is ``R = U @ D @ Vt`` (``D`` fixes the determinant sign), and the
    aligned moving set is ``Y @ R``.

    Empty input or mismatched atom counts return the legacy ``999.9`` sentinel;
    non-finite coordinates also return the sentinel instead of leaking NaN.
    """
    if coords1.shape[0] != coords2.shape[0] or coords1.shape[0] == 0:
        return 999.9
    n = coords1.shape[0]
    for i in range(n):
        for j in range(3):
            if not np.isfinite(coords1[i, j]) or not np.isfinite(coords2[i, j]):
                return 999.9
    center1 = coords1.sum(axis=0) / n
    center2 = coords2.sum(axis=0) / n
    x_centered = coords1 - center1
    y_centered = coords2 - center2
    h = np.dot(y_centered.T, x_centered)
    u, _s, vt = np.linalg.svd(h)
    d = np.eye(3)
    if np.linalg.det(np.dot(u, vt)) >= 0.0:
        d[-1, -1] = 1.0
    else:
        d[-1, -1] = -1.0
    rotation = np.dot(np.dot(u, d), vt)
    y_aligned = np.dot(y_centered, rotation)
    diff = x_centered - y_aligned
    return np.sqrt(np.sum(diff * diff) / n)


def kabsch_rmsd(coords1, coords2) -> float:
    """Pure-NumPy proper Kabsch RMSD used by graph-mapped comparisons.

    Uses the same formulation as :func:`fast_rmsd` but is not JIT compiled and
    returns a Python float.
    """
    x = np.asarray(coords1, dtype=np.float64)
    y = np.asarray(coords2, dtype=np.float64)
    if x.shape[0] != y.shape[0] or x.shape[0] == 0:
        return 999.9
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        return 999.9
    x_centered = x - x.mean(axis=0)
    y_centered = y - y.mean(axis=0)
    h = y_centered.T @ x_centered
    u, _s, vt = np.linalg.svd(h)
    d = np.eye(3)
    d[-1, -1] = 1.0 if np.linalg.det(u @ vt) >= 0.0 else -1.0
    rotation = u @ d @ vt
    aligned = y_centered @ rotation
    return float(np.sqrt(np.mean(np.sum((x_centered - aligned) ** 2, axis=1))))


@numba.njit(fastmath=True, cache=True)
def greedy_permutation_rmsd(coords1, coords2, elem_ids1, elem_ids2):
    """Legacy element-greedy matcher (compatibility entry point only).

    This helper is not used by the deduplication path any more: it matches
    atoms by element without honouring the bonding graph and can accept
    geometrically close but topologically different assignments.
    """
    n = coords1.shape[0]
    if n == 0 or coords2.shape[0] != n:
        return 999.9

    center1 = coords1.sum(axis=0) / n
    center2 = coords2.sum(axis=0) / n
    c1 = coords1 - center1
    c2 = coords2 - center2

    _, V1 = get_principal_axes(coords1)
    _, V2 = get_principal_axes(coords2)

    # 4 sign variants that preserve right-handedness (product of signs = +1)
    signs = np.array(
        [
            [1.0, 1.0, 1.0],
            [1.0, -1.0, -1.0],
            [-1.0, 1.0, -1.0],
            [-1.0, -1.0, 1.0],
        ]
    )

    best_rmsd = 999.9

    for si in range(4):
        # Rotation: R = V2 @ diag(s) @ V1^T
        S = np.diag(signs[si])
        R = np.ascontiguousarray(V2 @ S @ V1.T)
        c2_aligned = c2 @ R

        # Greedy matching: for each atom in c1, find nearest unassigned
        # same-element atom in c2_aligned
        assigned = np.zeros(n, dtype=np.bool_)
        sum_sq = 0.0
        valid = True

        for i in range(n):
            best_dist_sq = 1e18
            best_j = -1
            for j in range(n):
                if assigned[j]:
                    continue
                if elem_ids1[i] != elem_ids2[j]:
                    continue
                dx = c1[i, 0] - c2_aligned[j, 0]
                dy = c1[i, 1] - c2_aligned[j, 1]
                dz = c1[i, 2] - c2_aligned[j, 2]
                dist_sq = dx * dx + dy * dy + dz * dz
                if dist_sq < best_dist_sq:
                    best_dist_sq = dist_sq
                    best_j = j
            if best_j < 0:
                valid = False
                break
            assigned[best_j] = True
            sum_sq += best_dist_sq

        if valid:
            rmsd = np.sqrt(sum_sq / n)
            if rmsd < best_rmsd:
                best_rmsd = rmsd

    return best_rmsd


# ---------------------------------------------------------------------------
# Graph-constrained pair comparison
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PairVerdict:
    """Outcome of comparing one candidate against one retained representative."""

    status: str  # duplicate | distinct | different_topology | unresolved | invalid
    witness_rmsd: float | None = None
    cutoff: float = 0.0
    mapping: tuple[int, ...] | None = None
    mapping_kind: str = "none"  # identity | graph_mapping | none
    search_complete: bool = False
    search_work: MappingSearchStats | None = None
    reason: str = ""


def _frame_graph(frame: dict) -> Graph | None:
    graph = frame.get("graph")
    if graph is not None:
        return cast(Graph, graph)
    if "graph_status" in frame and frame.get("graph_status") != "ok":
        return None
    build = build_graph(frame.get("atoms", []), frame.get("coords", np.empty((0, 3))))
    frame["graph"] = build.graph
    frame["graph_status"] = build.status
    frame["graph_reason"] = build.reason
    return build.graph


def _frame_sort_key(frame: dict) -> tuple[float, int]:
    energy = frame.get("energy", float("inf"))
    try:
        energy_value = float(energy)
    except (TypeError, ValueError):
        energy_value = float("inf")
    if not np.isfinite(energy_value):
        energy_value = float("inf")
    return (energy_value, int(frame.get("original_index", 0)))


def _effective_cutoff(
    candidate: dict, representative: dict, threshold: float, energy_tolerance: float
) -> float:
    """Energy-assisted cutoff relaxation, guarded against missing energies."""
    cutoff = float(threshold)
    try:
        cand_energy = float(candidate.get("energy", float("inf")))
        rep_energy = float(representative.get("energy", float("inf")))
    except (TypeError, ValueError):
        return cutoff
    if not np.isfinite(cand_energy) or not np.isfinite(rep_energy):
        return cutoff
    if energy_tolerance <= 0:
        # tolerance 0 must genuinely disable the relaxation, including the
        # exactly-equal-energy case.
        return cutoff
    energy_diff = abs(cand_energy - rep_energy) * HARTREE_TO_KCALMOL
    if energy_diff <= energy_tolerance:
        return cutoff * ENERGY_RMSD_SCALE_FACTOR
    return cutoff


def _element_distance_fingerprint(
    atomic_numbers: Sequence[int],
    coords: np.ndarray,
    elements: Sequence[int] | None = None,
) -> np.ndarray:
    """Rigid-invariant per-atom fingerprint: nearest distance per element.

    The fingerprint is invariant under translation, rotation and atom
    renumbering, so identical atoms of rigid copies receive identical rows.
    It is used only to order the exact graph search (never to accept a match).
    """
    numbers = np.asarray(atomic_numbers)
    coordinates = np.asarray(coords, dtype=np.float64)
    if elements is None:
        elements = sorted({int(number) for number in numbers.tolist()})
    element_list = [int(element) for element in elements]
    n = len(numbers)
    diff = coordinates[:, None, :] - coordinates[None, :, :]
    distances = np.sqrt(np.sum(diff * diff, axis=-1))
    distances[np.arange(n), np.arange(n)] = np.inf
    fingerprint = np.empty((n, len(element_list)), dtype=np.float64)
    for column, element in enumerate(element_list):
        mask = numbers == element
        if not np.any(mask):
            fingerprint[:, column] = 0.0
            continue
        column_distances = distances[:, mask].min(axis=1)
        fingerprint[:, column] = np.nan_to_num(column_distances, posinf=0.0, nan=0.0)
    return fingerprint


def _build_candidate_priority(
    graph_candidate: Graph,
    graph_representative: Graph,
    cand_coords: np.ndarray,
    rep_coords: np.ndarray,
) -> dict[int, tuple[int, ...]] | None:
    """Order search candidates by fingerprint similarity (heuristic only).

    Every produced candidate list is still filtered and validated by the exact
    graph search, so ordering cannot admit an illegal mapping.  The mapping
    maps candidate indices to representative indices, so the candidate lists
    are always representative indices; both sides keep all atoms (including H).
    """
    positions_b: dict[int, list[int]] = {}
    for index in range(graph_representative.n):
        positions_b.setdefault(graph_representative.atomic_numbers[index], []).append(index)
    elements = sorted(
        set(graph_candidate.atomic_numbers) | set(graph_representative.atomic_numbers)
    )
    fingerprint_candidate = _element_distance_fingerprint(
        graph_candidate.atomic_numbers, cand_coords, elements
    )
    fingerprint_representative = _element_distance_fingerprint(
        graph_representative.atomic_numbers, rep_coords, elements
    )

    priority: dict[int, tuple[int, ...]] = {}
    for index in range(graph_candidate.n):
        element = graph_candidate.atomic_numbers[index]
        candidates = positions_b.get(element)
        if not candidates:
            return None
        scored = sorted(
            (
                float(
                    np.sum((fingerprint_candidate[index] - fingerprint_representative[other]) ** 2)
                ),
                other,
            )
            for other in candidates
        )
        priority[index] = tuple(other for _, other in scored)
    return priority


def compare_frames(
    candidate: dict,
    representative: dict,
    *,
    threshold: float,
    heavy_only: bool = False,
    energy_tolerance: float = 0.05,
    node_budget: int = DEFAULT_MAPPING_NODE_BUDGET,
) -> PairVerdict:
    """Decide whether *candidate* duplicates *representative* under legal mappings.

    The bonding graph is authoritative: RMSD is only evaluated for complete
    element/edge-preserving bijections.  The identity mapping is tried first
    when it is legal; otherwise, or when it fails the cutoff, the bounded
    mapping search continues.  A match yields a witness (not a proven global
    minimum); exhausting the search yields ``distinct``; running out of budget
    yields ``unresolved``.
    """
    graph_candidate = _frame_graph(candidate)
    graph_representative = _frame_graph(representative)
    if graph_candidate is None or graph_representative is None:
        return PairVerdict("invalid", reason="invalid graph")
    if graph_candidate.n != graph_representative.n:
        return PairVerdict("different_topology", reason="size_mismatch", search_complete=True)
    if not graphs_may_be_isomorphic(graph_candidate, graph_representative):
        return PairVerdict("different_topology", reason="invariant_mismatch", search_complete=True)

    cand_coords = np.asarray(candidate["coords"], dtype=np.float64)
    rep_coords = np.asarray(representative["coords"], dtype=np.float64)
    if cand_coords.shape != rep_coords.shape:
        return PairVerdict(
            "different_topology", reason="coordinate_shape_mismatch", search_complete=True
        )

    cutoff = _effective_cutoff(candidate, representative, threshold, energy_tolerance)
    # The comparison domain belongs to the *candidate* graph: mapping goes
    # candidate index -> representative index, so heavy indices must be read
    # from the candidate and mapped forward into the representative.
    candidate_heavy_indices = [
        i for i, number in enumerate(graph_candidate.atomic_numbers) if number != 1
    ]
    if heavy_only and not candidate_heavy_indices:
        return PairVerdict(
            "unresolved",
            cutoff=cutoff,
            reason="empty_comparison_domain",
            search_complete=False,
        )
    comparison_indices = candidate_heavy_indices if heavy_only else list(range(graph_candidate.n))

    def evaluate(mapping: tuple[int, ...]) -> float:
        target = rep_coords[[mapping[i] for i in comparison_indices]]
        moving = cand_coords[comparison_indices]
        return kabsch_rmsd(target, moving)

    seen_projections: set[tuple[int, ...]] = set()

    if fixed_index_isomorphism(graph_candidate, graph_representative):
        identity = tuple(range(graph_candidate.n))
        if heavy_only:
            seen_projections.add(tuple(identity[i] for i in comparison_indices))
        else:
            seen_projections.add(identity)
        rmsd = evaluate(identity)
        if rmsd < cutoff:
            return PairVerdict(
                "duplicate",
                witness_rmsd=rmsd,
                cutoff=cutoff,
                mapping=identity,
                mapping_kind="identity",
                search_complete=False,
                reason="identity_mapping_within_cutoff",
            )

    priority = _build_candidate_priority(
        graph_candidate,
        graph_representative,
        cand_coords,
        rep_coords,
    )
    search = MappingSearch(
        graph_candidate,
        graph_representative,
        node_budget,
        # The pairwise-distance lower bound only holds for a full-atom
        # comparison domain; noH uses the exact search without this prune.
        rmsd_cutoff=None if heavy_only else cutoff,
        coords_a=None if heavy_only else cand_coords,
        coords_b=None if heavy_only else rep_coords,
        candidate_priority=priority,
    )
    any_mapping = False
    try:
        for mapping in search.iter_mappings():
            any_mapping = True
            projection = tuple(mapping[i] for i in comparison_indices)
            if projection in seen_projections:
                continue
            seen_projections.add(projection)
            rmsd = evaluate(mapping)
            if rmsd < cutoff:
                return PairVerdict(
                    "duplicate",
                    witness_rmsd=rmsd,
                    cutoff=cutoff,
                    mapping=mapping,
                    mapping_kind=(
                        "identity"
                        if fixed_index_isomorphism(graph_candidate, graph_representative)
                        and mapping == tuple(range(graph_candidate.n))
                        else "graph_mapping"
                    ),
                    search_complete=False,
                    search_work=search.stats(),
                    reason="legal_mapping_within_cutoff",
                )
    except MappingBudgetExceeded:
        return PairVerdict(
            "unresolved",
            cutoff=cutoff,
            search_complete=False,
            search_work=search.stats(),
            reason="mapping_budget_exhausted",
        )

    if not any_mapping:
        if search.pruned:
            # Every branch was either evaluated above the cutoff or pruned by
            # the rigorous pairwise-distance bound, so no legal mapping can be
            # a duplicate; the graphs are still isomorphic candidates.
            return PairVerdict(
                "distinct",
                cutoff=cutoff,
                search_complete=True,
                search_work=search.stats(),
                reason="all_legal_mappings_pruned_above_cutoff",
            )
        return PairVerdict(
            "different_topology",
            cutoff=cutoff,
            search_complete=True,
            search_work=search.stats(),
            reason="no_legal_mapping_exists",
        )
    return PairVerdict(
        "distinct",
        cutoff=cutoff,
        search_complete=True,
        search_work=search.stats(),
        reason="all_legal_mappings_above_cutoff",
    )


# ---------------------------------------------------------------------------
# Worker / compatibility functions
# ---------------------------------------------------------------------------


def check_one_against_many(args):
    """Compare one candidate against a snapshot of retained conformers.

    Compatibility wrapper kept for external callers.  Graphs are rebuilt from
    the element ids and coordinates, so the comparison is constrained by the
    bonding graph rather than by unconstrained element matching.
    """
    cand_data, unique_data_snapshot, rmsd_threshold, energy_tolerance = args
    cand_coords, cand_pmi, cand_elem_ids, cand_energy = cand_data
    if cand_coords.shape[0] == 0:
        return False, -1
    cand_build = build_graph_from_atomic_numbers(cand_elem_ids, cand_coords)
    if cand_build.graph is None:
        return False, -1
    cand_frame = {
        "coords": cand_coords,
        "atoms": [],
        "energy": cand_energy,
        "graph": cand_build.graph,
    }
    for (
        unique_coords,
        _unique_pmi,
        unique_id,
        unique_elem_ids,
        unique_energy,
    ) in unique_data_snapshot:
        rep_build = build_graph_from_atomic_numbers(unique_elem_ids, unique_coords)
        if rep_build.graph is None:
            continue
        rep_frame = {
            "coords": unique_coords,
            "atoms": [],
            "energy": unique_energy,
            "graph": rep_build.graph,
        }
        verdict = compare_frames(
            cand_frame,
            rep_frame,
            threshold=rmsd_threshold,
            heavy_only=False,
            energy_tolerance=energy_tolerance,
        )
        if verdict.status == "duplicate":
            return True, unique_id
    return False, -1


def get_topology_hash_worker(args):
    """Legacy one-layer topology fingerprint (non-authoritative pre-filter).

    Kept for compatibility and cheap batching.  Equality of these hashes is
    necessary but never sufficient for equal topology; authoritative grouping
    must confirm with exact graph matching.
    """
    try:
        atoms, coords = args
        if len(atoms) == 0:
            return "empty"

        numbers = [get_element_atomic_number(atom) for atom in atoms]
        adj = build_adjacency(numbers, coords, bond_scale=BOND_SCALE_FACTOR)

        desc = []
        for i in range(len(atoms)):
            neighs = sorted([atoms[k] for k in adj[i]])
            desc.append(f"{atoms[i]}-({''.join(neighs)})")
        return hashlib.sha1("".join(sorted(desc)).encode()).hexdigest()
    except (ValueError, TypeError, IndexError, KeyError):
        return "error"


# ---------------------------------------------------------------------------
# Representative-based group deduplication
# ---------------------------------------------------------------------------


def _prepare_group_frame(frame: dict, heavy_atoms_only: bool) -> None:
    """Attach graph and comparison-domain data to a frame (in place)."""
    _frame_graph(frame)
    atoms = frame.get("atoms", []) or []
    coords = (
        np.asarray(frame["coords"], dtype=np.float64) if "coords" in frame else np.empty((0, 3))
    )
    if heavy_atoms_only:
        mask = np.array([str(atom) != "H" for atom in atoms], dtype=bool)
        heavy_coords = (
            coords[mask] if np.any(mask) and coords.shape[0] == len(atoms) else np.empty((0, 3))
        )
        atoms_filtered = [atom for atom, keep in zip(atoms, mask) if keep]
    else:
        atoms_filtered = list(atoms)
        heavy_coords = coords
    frame["heavy_coords"] = heavy_coords
    frame["heavy_elem_ids"] = np.array(
        [get_element_atomic_number(atom) for atom in atoms_filtered], dtype=np.int32
    )
    frame["pmi"] = get_pmi(coords)


def _report_entry(
    frame: dict,
    status: str,
    *,
    representative_id: int | str = "-",
    reason: str = "",
    witness_rmsd: float | None = None,
    cutoff: float | None = None,
    mapping_kind: str | None = None,
    mapping: tuple[int, ...] | None = None,
    search_complete: bool | None = None,
    search_work: MappingSearchStats | None = None,
) -> dict:
    entry = {
        "Input_Frame_ID": frame.get("original_index"),
        "Status": status,
        "Duplicate_Of_Input_ID": representative_id,
        "Reason": reason,
    }
    if witness_rmsd is not None:
        entry["Witness_RMSD"] = float(witness_rmsd)
    if cutoff is not None:
        entry["Cutoff"] = float(cutoff)
    if mapping_kind is not None:
        entry["Mapping"] = mapping_kind
    if mapping is not None:
        # Candidate index -> representative index, replayable by reviewers.
        entry["Mapping_Permutation"] = [int(index) for index in mapping]
    if search_complete is not None:
        entry["Search_Complete"] = bool(search_complete)
    if search_work is not None:
        entry["Search_Work"] = {
            "nodes": search_work.nodes,
            "mappings": search_work.mappings,
            "node_budget": search_work.node_budget,
            "complete": search_work.complete,
            "reason": search_work.reason,
            "pruned": search_work.pruned,
        }
    return entry


def process_topology_group(
    frames_in_group,
    rmsd_threshold,
    heavy_atoms_only,
    workers,
    energy_tolerance=0.05,
    mapping_budget=DEFAULT_MAPPING_NODE_BUDGET,
):
    """Deduplicate one confirmed topology class against retained representatives.

    Frames are processed in ``(energy, input index)`` order, so the outcome is
    independent of worker count or batching.  Every candidate is compared
    directly with each already-retained representative; removals always point
    to the representative that actually matched.  An incomplete mapping search
    keeps the candidate and reports it as unresolved.
    """
    frames_in_group = sorted(frames_in_group, key=_frame_sort_key)
    unique_frames, report_data = [], []
    if not frames_in_group:
        return [], []

    for frame in frames_in_group:
        _prepare_group_frame(frame, heavy_atoms_only)

    first = frames_in_group[0]
    unique_frames.append(first)
    report_data.append(_report_entry(first, "Kept", reason="group_representative"))

    for candidate in frames_in_group[1:]:
        removed = False
        unresolved = False
        unresolved_reason = ""
        unresolved_work: MappingSearchStats | None = None
        last_cutoff: float | None = None
        for representative in unique_frames:
            verdict = compare_frames(
                candidate,
                representative,
                threshold=rmsd_threshold,
                heavy_only=heavy_atoms_only,
                energy_tolerance=energy_tolerance,
                node_budget=mapping_budget,
            )
            last_cutoff = verdict.cutoff
            if verdict.status == "duplicate":
                report_data.append(
                    _report_entry(
                        candidate,
                        "Removed (Duplicate)",
                        representative_id=representative.get("original_index"),
                        reason=verdict.reason,
                        witness_rmsd=verdict.witness_rmsd,
                        cutoff=verdict.cutoff,
                        mapping_kind=verdict.mapping_kind,
                        mapping=verdict.mapping,
                        search_complete=verdict.search_complete,
                        search_work=verdict.search_work,
                    )
                )
                removed = True
                break
            if verdict.status == "unresolved":
                unresolved = True
                unresolved_reason = verdict.reason
                unresolved_work = verdict.search_work
        if removed:
            continue

        unique_frames.append(candidate)
        if unresolved:
            report_data.append(
                _report_entry(
                    candidate,
                    "Kept (Unresolved)",
                    reason=unresolved_reason,
                    cutoff=last_cutoff,
                    search_complete=False,
                    search_work=unresolved_work,
                )
            )
        else:
            report_data.append(
                _report_entry(candidate, "Kept", reason="distinct", cutoff=last_cutoff)
            )

    return unique_frames, report_data
