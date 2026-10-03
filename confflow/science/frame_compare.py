#!/usr/bin/env python3

"""Graph-constrained frame comparison (moved unchanged from refine's RMSD engine).

Geometry comparisons are only performed through legal graph mappings produced
by :mod:`confflow.science.topology_mapping`; a fixed-index RMSD is never assumed
to be the minimum over symmetry-equivalent mappings.  The function bodies are
the legacy ``confflow.blocks.refine.rmsd_engine`` ones, byte for byte.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

import numpy as np

from ..core.constants import HARTREE_TO_KCALMOL
from .topology_mapping import (
    DEFAULT_MAPPING_NODE_BUDGET,
    Graph,
    MappingBudgetExceeded,
    MappingSearch,
    MappingSearchStats,
    build_graph,
    fixed_index_isomorphism,
    graphs_may_be_isomorphic,
)

ENERGY_RMSD_SCALE_FACTOR = 1.5

__all__ = [
    "ENERGY_RMSD_SCALE_FACTOR",
    "PairVerdict",
    "compare_frames",
    "kabsch_rmsd",
]


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
        # Vectorised squared fingerprint distances for this candidate atom
        # against every same-element representative atom.  ``positions_b`` is
        # built in ascending index order and ``argsort(kind="stable")`` keeps
        # that order for equal scores, reproducing the previous
        # ``sorted((score, other))`` tie-breaking exactly.  The per-row
        # reduction matches the previous per-pair ``np.sum`` on a 1-D row.
        candidate_indices = np.asarray(candidates, dtype=np.intp)
        diff = fingerprint_representative[candidate_indices] - fingerprint_candidate[index]
        scores = np.sum(diff * diff, axis=1)
        order = np.argsort(scores, kind="stable")
        priority[index] = tuple(int(candidate_indices[position]) for position in order)
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
