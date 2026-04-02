#!/usr/bin/env python3

"""RMSD deduplication engine — core computation functions.

Split from processor.py. Contains Numba JIT-accelerated RMSD/PMI calculation,
topology hashing, and batch deduplication logic.
"""

from __future__ import annotations

import hashlib
import logging
from concurrent.futures import ProcessPoolExecutor
from itertools import repeat

import numpy as np

from ._compat import (
    load_console_bindings,
    load_hartree_to_kcal,
    load_numba_runtime,
    load_refine_data,
)

logger = logging.getLogger("confflow.refine")

# ---------------------------------------------------------------------------
# Dependency imports (with fallback)
# ---------------------------------------------------------------------------

create_progress = load_console_bindings()["create_progress"]
PERIODIC_SYMBOLS, GV_COVALENT_RADII = load_refine_data()
HARTREE_TO_KCALMOL = load_hartree_to_kcal()

# ---------------------------------------------------------------------------
# Numba & constants
# ---------------------------------------------------------------------------

numba = load_numba_runtime("confflow.refine")

__all__ = [
    "BOND_SCALE_FACTOR",
    "PMI_TOLERANCE_FACTOR",
    "ENERGY_RMSD_SCALE_FACTOR",
    "get_element_atomic_number",
    "get_pmi",
    "get_principal_axes",
    "fast_rmsd",
    "greedy_permutation_rmsd",
    "check_one_against_many",
    "get_topology_hash_worker",
    "process_topology_group",
]

BOND_SCALE_FACTOR = 1.2
PMI_TOLERANCE_FACTOR = 0.05
ENERGY_RMSD_SCALE_FACTOR = 1.5


# ---------------------------------------------------------------------------
# Element utilities
# ---------------------------------------------------------------------------


def get_element_atomic_number(symbol: str) -> int:
    """Get atomic number from element symbol."""
    if not symbol:
        return 0
    s = symbol.capitalize()
    try:
        return PERIODIC_SYMBOLS.index(s)
    except ValueError:
        return 0


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
    Used by :func:`greedy_permutation_rmsd` for axis-aligned matching.
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
    if coords1.shape[0] != coords2.shape[0] or coords1.shape[0] == 0:
        return 999.9
    center1 = coords1.sum(axis=0) / coords1.shape[0]
    center2 = coords2.sum(axis=0) / coords2.shape[0]
    coords1_centered = coords1 - center1
    coords2_centered = coords2 - center2
    C = np.dot(coords2_centered.T, coords1_centered)
    U, S, Vt = np.linalg.svd(C)
    d = np.linalg.det(Vt.T) * np.linalg.det(U)
    if d < 0:
        S[-1] = -S[-1]
        Vt[-1, :] = -Vt[-1, :]
    R = np.dot(Vt.T, U.T)
    coords2_aligned = np.dot(coords2_centered, R)
    diff = coords1_centered - coords2_aligned
    return np.sqrt(np.sum(diff * diff) / coords1.shape[0])


@numba.njit(fastmath=True, cache=True)
def greedy_permutation_rmsd(coords1, coords2, elem_ids1, elem_ids2):
    """Symmetry-aware RMSD via principal-axis alignment + greedy element matching.

    Tries 4 axis-sign variants (preserving handedness), performs greedy
    nearest-neighbour matching within the same element type, and returns
    the minimum RMSD across all variants.
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
# Worker functions
# ---------------------------------------------------------------------------


def check_one_against_many(args):
    """Compare one candidate conformer against a snapshot of unique conformers.

    Supports dual RMSD validation: fast Kabsch first, then symmetry-aware
    greedy fallback when PMI passes but fast_rmsd exceeds the threshold.
    Energy-assisted threshold relaxation is applied when ΔE ≤ *energy_tolerance*.
    """
    cand_data, unique_data_snapshot, rmsd_threshold, energy_tolerance = args
    cand_coords, cand_pmi, cand_elem_ids, cand_energy = cand_data
    if cand_coords.shape[0] == 0:
        return False, -1
    for (
        unique_coords,
        unique_pmi,
        unique_id,
        unique_elem_ids,
        unique_energy,
    ) in unique_data_snapshot:
        pmi_diff = np.abs(cand_pmi - unique_pmi)
        pmi_tol = (unique_pmi + cand_pmi) * 0.5 * PMI_TOLERANCE_FACTOR
        if np.any(pmi_diff > pmi_tol + 1e-4):
            continue

        # Energy-assisted threshold relaxation
        energy_diff = abs(cand_energy - unique_energy) * HARTREE_TO_KCALMOL
        effective_threshold = rmsd_threshold
        if energy_diff <= energy_tolerance:
            effective_threshold = rmsd_threshold * ENERGY_RMSD_SCALE_FACTOR

        # Fast path: standard Kabsch RMSD
        rmsd = fast_rmsd(cand_coords, unique_coords)
        if rmsd < effective_threshold:
            return True, unique_id

        # Slow path: symmetry-aware RMSD (principal-axis + greedy matching)
        perm_rmsd = greedy_permutation_rmsd(
            cand_coords, unique_coords, cand_elem_ids, unique_elem_ids
        )
        if perm_rmsd < effective_threshold:
            return True, unique_id
    return False, -1


def get_topology_hash_worker(args):
    """Compute topology hash for a set of atoms and coordinates."""
    try:
        atoms, coords = args
        if len(atoms) == 0:
            return "empty"

        tuple(sorted(atoms))

        radii = np.array([GV_COVALENT_RADII[get_element_atomic_number(a)] for a in atoms])

        # P2-3: Use cKDTree instead of O(N²) full distance matrix.
        # For N=500, the full matrix is ~3 MB per worker; cKDTree reduces that
        # to only the pairs within the bonding threshold.
        from scipy.spatial import cKDTree  # scipy is a listed dependency

        n = len(atoms)
        max_threshold = float(np.max(radii) * 2.0 * BOND_SCALE_FACTOR)
        tree = cKDTree(coords)
        candidate_pairs = tree.query_pairs(max_threshold, output_type="ndarray")

        adj: list[list[int]] = [[] for _ in range(n)]
        for i, j in candidate_pairs:
            ri, rj = radii[i], radii[j]
            threshold = (ri + rj) * BOND_SCALE_FACTOR
            diff = coords[i] - coords[j]
            dist_sq = float(np.dot(diff, diff))
            if dist_sq < threshold * threshold:
                adj[i].append(j)
                adj[j].append(i)

        desc = []
        for i in range(n):
            neighs = sorted([atoms[k] for k in adj[i]])
            desc.append(f"{atoms[i]}-({''.join(neighs)})")
        return hashlib.sha1("".join(sorted(desc)).encode()).hexdigest()
    except (ValueError, TypeError, IndexError, KeyError):
        return "error"


def process_topology_group(
    frames_in_group, rmsd_threshold, heavy_atoms_only, workers, energy_tolerance=0.05
):
    frames_in_group.sort(key=lambda x: x["energy"])
    unique_frames, report_data = [], []
    if not frames_in_group:
        return [], []

    for f in frames_in_group:
        coords = f["coords"]
        atoms = f["atoms"]
        if heavy_atoms_only:
            mask = np.array([a != "H" for a in atoms])
            coords = coords[mask] if np.any(mask) else np.empty((0, 3))
            atoms_filtered = [a for a, m in zip(atoms, mask) if m]
        else:
            atoms_filtered = list(atoms)
        f["heavy_coords"] = coords
        f["heavy_elem_ids"] = np.array(
            [get_element_atomic_number(a) for a in atoms_filtered], dtype=np.int32
        )
        f["pmi"] = get_pmi(coords)

    first = frames_in_group.pop(0)
    unique_frames.append(first)
    report_data.append(
        {"Input_Frame_ID": first["original_index"], "Status": "Kept", "Duplicate_Of_Input_ID": "-"}
    )

    candidates = frames_in_group
    if not candidates:
        return unique_frames, report_data

    BATCH_SIZE = min(len(candidates), max(100, workers * 20))
    with ProcessPoolExecutor(max_workers=workers) as executor:
        with create_progress() as progress:
            task_id = progress.add_task("[cyan]RMSD dedup", total=len(candidates))
            while candidates:
                curr_batch = candidates[:BATCH_SIZE]
                candidates = candidates[BATCH_SIZE:]
                unique_snap = [
                    (
                        u["heavy_coords"],
                        u["pmi"],
                        u["original_index"],
                        u["heavy_elem_ids"],
                        u["energy"],
                    )
                    for u in unique_frames
                ]
                batch_data = [
                    (c["heavy_coords"], c["pmi"], c["heavy_elem_ids"], c["energy"])
                    for c in curr_batch
                ]

                chunk = max(1, len(curr_batch) // (workers * 4) + 1)
                results = list(
                    executor.map(
                        check_one_against_many,
                        zip(
                            batch_data,
                            repeat(unique_snap),
                            repeat(rmsd_threshold),
                            repeat(energy_tolerance),
                        ),
                        chunksize=chunk,
                    )
                )

                newly_kept: list[dict] = []
                for i, cand in enumerate(curr_batch):
                    is_dup, mid = results[i]
                    if is_dup:
                        report_data.append(
                            {
                                "Input_Frame_ID": cand["original_index"],
                                "Status": "Removed (Duplicate)",
                                "Duplicate_Of_Input_ID": mid,
                            }
                        )
                    else:
                        is_intra_dup = False
                        if newly_kept:
                            args = (
                                (
                                    cand["heavy_coords"],
                                    cand["pmi"],
                                    cand["heavy_elem_ids"],
                                    cand["energy"],
                                ),
                                [
                                    (
                                        k["heavy_coords"],
                                        k["pmi"],
                                        k["original_index"],
                                        k["heavy_elem_ids"],
                                        k["energy"],
                                    )
                                    for k in newly_kept
                                ],
                                rmsd_threshold,
                                energy_tolerance,
                            )
                            if check_one_against_many(args)[0]:
                                is_intra_dup = True

                        if not is_intra_dup:
                            newly_kept.append(cand)
                            report_data.append(
                                {
                                    "Input_Frame_ID": cand["original_index"],
                                    "Status": "Kept",
                                    "Duplicate_Of_Input_ID": "-",
                                }
                            )

                unique_frames.extend(newly_kept)
                progress.advance(task_id, advance=len(curr_batch))

    return unique_frames, report_data
