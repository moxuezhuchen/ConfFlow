#!/usr/bin/env python3
"""Targeted hotspot tests for refine.rmsd_engine."""

from __future__ import annotations

import importlib
import sys
from unittest.mock import patch

import numpy as np

import confflow.blocks.refine.rmsd_engine as rmsd_engine


def _reload_with_blocked_imports(module, blocked: set[str]):
    blocked_modules = {name: None for name in blocked}
    with patch.dict(sys.modules, blocked_modules):
        return importlib.reload(module)


def test_rmsd_engine_fallback_imports_and_python_execution():
    blocked = {
        "confflow.core.console",
        "confflow.core.utils",
        "confflow.core.data",
        "confflow.core.constants",
    }
    engine = _reload_with_blocked_imports(rmsd_engine, blocked)

    try:
        assert engine.numba.__name__ == "FakeNumba"
        assert engine.HARTREE_TO_KCALMOL > 600
        assert engine.GV_COVALENT_RADII[6] > 0
        assert engine.get_element_atomic_number("") == 0
        assert engine.get_element_atomic_number("not-an-element") == 0

        with engine.create_progress() as progress:
            task_id = progress.add_task("x")
            progress.advance(task_id)

        empty_pmi = engine.get_pmi(np.empty((0, 3), dtype=np.float64))
        assert np.allclose(empty_pmi, [0.0, 0.0, 0.0])

        coords = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [-1.0, 0.0, 0.0]], dtype=np.float64)
        assert engine.fast_rmsd(coords, np.empty((0, 3), dtype=np.float64)) == 999.9
        eigvals, eigvecs = engine.get_principal_axes(coords)
        assert eigvecs.shape == (3, 3)
        assert len(eigvals) == 3

        elem_ids = np.array([8, 1, 1], dtype=np.int32)
        swapped = np.array([[0.0, 0.0, 0.0], [-1.0, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=np.float64)
        assert engine.greedy_permutation_rmsd(coords, swapped, elem_ids, elem_ids.copy()) < 1e-6
        assert (
            engine.greedy_permutation_rmsd(
                coords,
                swapped,
                elem_ids,
                np.array([7, 6, 6], dtype=np.int32),
            )
            == 999.9
        )
    finally:
        importlib.reload(rmsd_engine)


def test_check_one_against_many_uses_legal_mapping_beyond_identity():
    """Identity mapping is legal but not exact; a graph automorphism confirms it."""
    path = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.5, 0.0, 0.0],
            [2.0, 1.4, 0.0],
            [1.0, 2.5, 0.2],
            [1.0, 3.0, 1.6],
        ],
        dtype=np.float64,
    )
    elem_ids = np.array([6, 6, 6, 6, 6], dtype=np.int32)
    pmi = np.zeros(3, dtype=np.float64)
    cand = (path[::-1].copy(), pmi, elem_ids, -10.0)
    unique = [(path.copy(), pmi.copy(), 7, elem_ids.copy(), -10.0)]

    assert rmsd_engine.fast_rmsd(path, path[::-1].copy()) > 0.25
    is_dup, match_id = rmsd_engine.check_one_against_many((cand, unique, 0.25, 0.05))
    assert is_dup is True
    assert match_id == 7


def test_check_one_against_many_pmi_difference_does_not_block_confirmed_duplicate():
    """PMI is not an authoritative exclusion criterion any more."""
    coords = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=np.float64)
    elem_ids = np.array([6, 6], dtype=np.int32)
    cand = (coords, np.array([1.0, 1.0, 1.0]), elem_ids, -10.0)
    unique = [(coords.copy(), np.array([100.0, 100.0, 100.0]), 8, elem_ids.copy(), -10.0)]

    is_dup, match_id = rmsd_engine.check_one_against_many((cand, unique, 0.5, 0.05))
    assert is_dup is True
    assert match_id == 8


def test_check_one_against_many_rejects_illegal_same_element_permutation():
    """A same-element pairing that breaks the bonding graph is not acceptable."""
    atom = np.array([6, 6, 6], dtype=np.int32)
    # Candidate: triangle (all pairs bonded).  Representative: path 1-2-3.
    triangle = np.array([[0.0, 0.0, 0.0], [1.5, 0.0, 0.0], [0.75, 1.3, 0.0]], dtype=np.float64)
    path = np.array([[0.0, 0.0, 0.0], [1.5, 0.0, 0.0], [3.0, 0.0, 0.0]], dtype=np.float64)
    pmi = np.zeros(3, dtype=np.float64)
    cand = (triangle, pmi, atom, -10.0)
    unique = [(path, pmi.copy(), 9, atom.copy(), -10.0)]

    is_dup, match_id = rmsd_engine.check_one_against_many((cand, unique, 100.0, 0.05))
    assert is_dup is False
    assert match_id == -1


def test_get_topology_hash_worker_invalid_element_returns_error():
    atoms = ["C"]
    coords = None
    assert rmsd_engine.get_topology_hash_worker((atoms, coords)) == "error"


def test_process_topology_group_empty_returns_empty():
    unique, report = rmsd_engine.process_topology_group([], 0.25, False, 1)
    assert unique == []
    assert report == []


def test_process_topology_group_heavy_atoms_only_all_hydrogen():
    frames = [
        {
            "original_index": 1,
            "energy": -1.0,
            "atoms": ["H", "H"],
            "coords": np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64),
        }
    ]

    unique, report = rmsd_engine.process_topology_group(frames, 0.25, True, 1)
    assert len(unique) == 1
    assert unique[0]["heavy_coords"].shape == (0, 3)
    assert unique[0]["heavy_elem_ids"].size == 0
    assert report[0]["Status"] == "Kept"


def _triangle(scale):
    radius = (1.0 / np.sqrt(3.0)) * scale
    angles = np.deg2rad([90.0, 210.0, 330.0])
    return np.column_stack([radius * np.cos(angles), radius * np.sin(angles), np.zeros(3)])


def test_process_topology_group_marks_intra_batch_duplicate_with_representative():
    """C duplicates B directly (A is too far); the report must say so."""
    frames = [
        {
            "original_index": 0,
            "energy": -3.0,
            "atoms": ["C", "C", "C"],
            "coords": _triangle(1.0),
        },
        {
            "original_index": 1,
            "energy": -2.0,
            "atoms": ["C", "C", "C"],
            "coords": _triangle(1.2),
        },
        {
            "original_index": 2,
            "energy": -1.0,
            "atoms": ["C", "C", "C"],
            "coords": _triangle(1.3),
        },
    ]

    unique, report = rmsd_engine.process_topology_group(frames, 0.1, False, 1)

    assert [frame["original_index"] for frame in unique] == [0, 1]
    removed = report[2]
    assert removed["Input_Frame_ID"] == 2
    assert removed["Status"] == "Removed (Duplicate)"
    assert removed["Duplicate_Of_Input_ID"] == 1
    assert removed["Witness_RMSD"] < 0.1
    assert removed["Reason"] == "identity_mapping_within_cutoff"
