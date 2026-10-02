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
from typing import cast

import numpy as np

from ...core.bonding import build_adjacency
from ...science.frame_compare import (  # noqa: F401
    ENERGY_RMSD_SCALE_FACTOR,
    PairVerdict,
    _build_candidate_priority,
    _effective_cutoff,
    _element_distance_fingerprint,
    _frame_graph,
    compare_frames,
    kabsch_rmsd,
)
from ._compat import (
    load_console_bindings,
    load_hartree_to_kcal,
    load_numba_runtime,
    load_refine_data,
)
from .topology import (
    DEFAULT_MAPPING_NODE_BUDGET,
    MappingSearchStats,
    build_graph_from_atomic_numbers,
    get_element_atomic_number,
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


def _frame_sort_key(frame: dict) -> tuple[float, int]:
    energy = frame.get("energy", float("inf"))
    try:
        energy_value = float(energy)
    except (TypeError, ValueError):
        energy_value = float("inf")
    if not np.isfinite(energy_value):
        energy_value = float("inf")
    return (energy_value, int(frame.get("original_index", 0)))


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
