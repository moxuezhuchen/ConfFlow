#!/usr/bin/env python3
"""Targeted hotspot tests for refine.rmsd_engine."""

from __future__ import annotations

import importlib
import sys
from unittest.mock import patch

import numpy as np

import confflow.blocks.refine.rmsd_engine as rmsd_engine


class _SyncExecutor:
    def __init__(self, *args, **kwargs):
        del args, kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        return False

    def map(self, func, iterable, chunksize=None):
        del chunksize
        return map(func, iterable)


class _Progress:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        return False

    def add_task(self, *args, **kwargs):
        del args, kwargs
        return 1

    def advance(self, *args, **kwargs):
        del args, kwargs
        return None


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


def test_check_one_against_many_uses_symmetry_fallback(monkeypatch):
    coords = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=np.float64)
    pmi = np.array([1.0, 2.0, 3.0], dtype=np.float64)
    elem_ids = np.array([6, 6], dtype=np.int32)
    cand = (coords, pmi, elem_ids, -10.0)
    unique = [(coords.copy(), pmi.copy(), 7, elem_ids.copy(), -10.0)]

    monkeypatch.setattr(rmsd_engine, "fast_rmsd", lambda *args, **kwargs: 9.0)
    monkeypatch.setattr(rmsd_engine, "greedy_permutation_rmsd", lambda *args, **kwargs: 0.01)

    is_dup, match_id = rmsd_engine.check_one_against_many((cand, unique, 0.5, 0.05))
    assert is_dup is True
    assert match_id == 7


def test_check_one_against_many_pmi_mismatch_short_circuits(monkeypatch):
    coords = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=np.float64)
    elem_ids = np.array([6, 6], dtype=np.int32)
    cand = (coords, np.array([1.0, 1.0, 1.0]), elem_ids, -10.0)
    unique = [(coords.copy(), np.array([100.0, 100.0, 100.0]), 8, elem_ids.copy(), -10.0)]

    monkeypatch.setattr(
        rmsd_engine,
        "fast_rmsd",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    is_dup, match_id = rmsd_engine.check_one_against_many((cand, unique, 0.5, 0.05))
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


def test_process_topology_group_marks_intra_batch_duplicate(monkeypatch):
    def fake_check(args):
        cand_data, unique_data_snapshot, rmsd_threshold, energy_tolerance = args
        del cand_data, rmsd_threshold, energy_tolerance
        ids = [item[2] for item in unique_data_snapshot]
        return ((1 in ids), 1 if 1 in ids else -1)

    frames = [
        {
            "original_index": 0,
            "energy": -2.0,
            "atoms": ["C"],
            "coords": np.array([[0.0, 0.0, 0.0]], dtype=np.float64),
        },
        {
            "original_index": 1,
            "energy": -1.0,
            "atoms": ["C"],
            "coords": np.array([[1.0, 0.0, 0.0]], dtype=np.float64),
        },
        {
            "original_index": 2,
            "energy": -0.5,
            "atoms": ["C"],
            "coords": np.array([[2.0, 0.0, 0.0]], dtype=np.float64),
        },
    ]

    monkeypatch.setattr(rmsd_engine, "ProcessPoolExecutor", _SyncExecutor)
    monkeypatch.setattr(rmsd_engine, "create_progress", lambda: _Progress())
    monkeypatch.setattr(rmsd_engine, "check_one_against_many", fake_check)

    unique, report = rmsd_engine.process_topology_group(frames, 0.25, False, 1)

    assert [frame["original_index"] for frame in unique] == [0, 1]
    assert report == [
        {"Input_Frame_ID": 0, "Status": "Kept", "Duplicate_Of_Input_ID": "-"},
        {"Input_Frame_ID": 1, "Status": "Kept", "Duplicate_Of_Input_ID": "-"},
    ]
