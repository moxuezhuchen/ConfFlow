#!/usr/bin/env python3

"""Tests for refine module (merged)."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from confflow.blocks import refine
from confflow.blocks.refine.processor import (
    RefineOptions,
    process_xyz,
    read_xyz_file,
)
from confflow.blocks.refine.rmsd_engine import (
    fast_rmsd,
    get_element_atomic_number,
    get_pmi,
    get_topology_hash_worker,
    greedy_permutation_rmsd,
)


def test_refine_options_default_output():
    opts = RefineOptions(input_file="test.xyz")
    assert opts.output == "test_cleaned.xyz"
    assert opts.threshold == 0.25
    assert opts.workers >= 1


def test_refine_options_basic():
    opts = RefineOptions(input_file="test.xyz")
    assert opts.input_file == "test.xyz"
    assert opts.threshold == 0.25


def test_get_pmi_empty_and_single_atom():
    coords = np.empty((0, 3))
    pmi = get_pmi(coords)
    assert np.all(pmi == 0.0)

    coords = np.array([[0.0, 0.0, 0.0]])
    pmi = get_pmi(coords)
    assert np.all(pmi == 0.0)


def test_get_pmi_basic():
    coords = np.array([[-1.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    pmi = get_pmi(coords)
    assert len(pmi) == 3


def test_fast_rmsd_variants():
    c1 = np.zeros((3, 3))
    c2 = np.zeros((4, 3))
    assert fast_rmsd(c1, c2) == 999.9

    c1 = np.empty((0, 3))
    c2 = np.empty((0, 3))
    assert fast_rmsd(c1, c2) == 999.9

    c1 = np.array([[0, 0, 0], [1, 0, 0]])
    c2 = np.array([[0, 0, 0], [1.1, 0, 0]])
    rmsd = fast_rmsd(c1, c2)
    assert rmsd == pytest.approx(0.05, abs=1e-4)


def test_read_xyz_file_nonexistent():
    assert read_xyz_file("nonexistent.xyz") == []


def test_read_xyz_file_basic(tmp_path):
    xyz = tmp_path / "test.xyz"
    xyz.write_text("2\nE=-1.0\nC 0 0 0\nC 1.5 0 0\n")
    frames = read_xyz_file(str(xyz))
    assert len(frames) == 1
    assert frames[0]["energy"] == -1.0
    assert frames[0]["atoms"] == ["C", "C"]
    assert frames[0]["coords"].shape == (2, 3)


def test_get_topology_hash_basic_and_empty():
    symbols = ["C", "C"]
    coords = np.array([[0, 0, 0], [1.5, 0, 0]])
    h = get_topology_hash_worker((symbols, coords))
    assert isinstance(h, str)
    assert (
        len(h) == 40
    )  # full SHA-1 digest (no truncation, prevents collisions with large conformer sets)

    assert get_topology_hash_worker(([], np.empty((0, 3)))) == "empty"


def test_get_topology_hash_worker_exception():
    assert get_topology_hash_worker(None) == "error"


def test_get_element_atomic_number():
    assert get_element_atomic_number("H") == 1
    assert get_element_atomic_number("C") == 6
    assert get_element_atomic_number("O") == 8
    assert get_element_atomic_number("Xx") == 0
    assert get_element_atomic_number("He") == 2
    assert get_element_atomic_number("Li") == 3
    assert get_element_atomic_number("U") == 92
    assert get_element_atomic_number("h") == 1


def test_process_xyz_basic(tmp_path):
    xyz_content = """3
E=-1.0
C 0.0 0.0 0.0
C 1.5 0.0 0.0
H 1.5 1.0 0.0
3
E=-1.1
C 0.0 0.0 0.0
C 1.5 0.0 0.0
H 1.5 1.0 0.0
"""
    in_xyz = tmp_path / "in.xyz"
    in_xyz.write_text(xyz_content)

    out_xyz = tmp_path / "out.xyz"
    opts = RefineOptions(str(in_xyz), output=str(out_xyz), threshold=0.1)

    process_xyz(opts)
    assert out_xyz.exists()
    with open(out_xyz) as f:
        lines = f.readlines()
    assert len(lines) == 5


def test_process_xyz_energy_filter(tmp_path):
    xyz_content = """3
E=-1.0
C 0.0 0.0 0.0
C 1.5 0.0 0.0
H 1.5 1.0 0.0
3
E=-0.5
C 0.0 0.0 0.0
C 1.5 0.0 0.0
H 1.5 1.0 1.0
"""
    in_xyz = tmp_path / "in.xyz"
    in_xyz.write_text(xyz_content)

    out_xyz = tmp_path / "out.xyz"
    opts = RefineOptions(str(in_xyz), output=str(out_xyz), ewin=10.0)

    process_xyz(opts)

    assert out_xyz.exists()
    with open(out_xyz) as f:
        lines = f.readlines()
    assert len(lines) == 5


def test_process_xyz_full(tmp_path):
    xyz = tmp_path / "input.xyz"
    xyz.write_text(
        "2\nE=-1.0\nC 0 0 0\nC 1.5 0 0\n"
        "2\nE=-1.0\nC 0 0 0\nC 1.5 0 0\n"
        "2\nE=-2.0\nC 0 0 0\nC 2.0 0 0\n"
    )

    opts = RefineOptions(
        input_file=str(xyz),
        output=str(tmp_path / "output.xyz"),
        threshold=0.1,
        keep_all_topos=True,
    )

    process_xyz(opts)

    assert os.path.exists(opts.output)
    with open(opts.output) as f:
        lines = f.readlines()
        assert lines.count("2\n") == 2


def test_process_xyz_no_energy(tmp_path):
    xyz = tmp_path / "input.xyz"
    xyz.write_text("2\nNo Energy\nC 0 0 0\nC 1.5 0 0\n")
    opts = RefineOptions(input_file=str(xyz), output=str(tmp_path / "output.xyz"))
    process_xyz(opts)
    assert os.path.exists(opts.output)


def test_process_xyz_sort_energy(tmp_path):
    xyz = tmp_path / "input.xyz"
    xyz.write_text("2\nE=-1.0\nC 0 0 0\nC 1.5 0 0\n" "2\nE=-2.0\nC 0 0 0\nC 2.0 0 0\n")
    opts = RefineOptions(input_file=str(xyz), output=str(tmp_path / "output.xyz"))
    process_xyz(opts)
    with open(opts.output) as f:
        content = f.read()
        assert content.find("E=-2.0") < content.find("E=-1.0")


def test_process_xyz_ewin_filter(tmp_path, sync_executor):

    xyz_content = """2
E=-10.000
C 0.0 0.0 0.0
H 0.0 0.0 1.0
2
E=-9.999
C 0.0 0.0 0.0
H 0.0 0.0 1.1
2
E=-9.000
C 0.0 0.0 0.0
H 0.0 0.0 1.2
"""
    in_xyz = tmp_path / "in.xyz"
    in_xyz.write_text(xyz_content)

    out_xyz = tmp_path / "out.xyz"
    opts = RefineOptions(str(in_xyz), output=str(out_xyz), ewin=2.0, threshold=0.01)

    process_xyz(opts)

    with open(out_xyz) as f:
        lines = f.readlines()
    assert len(lines) == 8


def test_process_xyz_no_energy_extended(tmp_path, sync_executor):

    xyz_content = """2
No Energy
C 0.0 0.0 0.0
H 0.0 0.0 1.0
2
No Energy
C 0.0 0.0 0.0
H 0.0 0.0 1.1
"""
    in_xyz = tmp_path / "in.xyz"
    in_xyz.write_text(xyz_content)

    out_xyz = tmp_path / "out.xyz"
    opts = RefineOptions(str(in_xyz), output=str(out_xyz), threshold=0.01)

    process_xyz(opts)
    assert out_xyz.exists()


def test_process_xyz_sort_only(tmp_path, sync_executor):

    xyz_content = """2
E=-5.0
C 0.0 0.0 0.0
H 0.0 0.0 1.0
2
E=-10.0
C 0.0 0.0 0.0
H 0.0 0.0 1.1
"""
    in_xyz = tmp_path / "in.xyz"
    in_xyz.write_text(xyz_content)

    out_xyz = tmp_path / "out.xyz"
    opts = RefineOptions(str(in_xyz), output=str(out_xyz), threshold=0.01)

    process_xyz(opts)

    with open(out_xyz) as f:
        lines = f.readlines()
    assert "E=-10.0" in lines[1]
    assert "E=-5.0" in lines[5]


def test_refine_fallback_imports():
    import confflow.blocks.refine.processor as processor
    import confflow.blocks.refine.rmsd_engine as engine

    with patch.dict(sys.modules, {"numba": None, "tqdm": None}):
        importlib.reload(engine)

        assert engine.numba.__name__ == "FakeNumba"

        @engine.numba.njit()
        def test_func(x):
            return x

        assert test_func(1) == 1

    # Restore outside the patch.dict block so real numba is available
    importlib.reload(engine)
    importlib.reload(processor)


def test_refine_covalent_radii_fallback():
    import confflow.blocks.refine.processor as processor
    import confflow.blocks.refine.rmsd_engine as engine

    with patch.dict(sys.modules, {"confflow.core.utils": None}):
        importlib.reload(engine)
        assert hasattr(engine, "GV_COVALENT_RADII")
        assert len(engine.GV_COVALENT_RADII) > 0

    # Restore outside the patch.dict block so real numba is available
    importlib.reload(engine)
    importlib.reload(processor)


def test_refine_preserves_ts_bond_in_comment(tmp_path: Path) -> None:
    inp = tmp_path / "result.xyz"
    inp.write_text(
        "2\nEnergy=-1.000000 TSBond=0.740000 TSAtoms=1,2\nH 0 0 0\nH 0 0 0.74\n",
        encoding="utf-8",
    )

    out = tmp_path / "output.xyz"
    args = refine.RefineOptions(
        input_file=str(inp),
        output=str(out),
        threshold=0.25,
        ewin=None,
        imag=None,
        noH=False,
        max_conformers=None,
        dedup_only=False,
        keep_all_topos=False,
        workers=1,
    )

    refine.process_xyz(args)
    text = out.read_text(encoding="utf-8")
    assert "TSBond=0.74" in text
    assert "TSAtoms=1,2" not in text


def test_refine_parses_imag_from_calc_output_format(tmp_path: Path) -> None:
    inp = tmp_path / "result.xyz"
    inp.write_text(
        "2\nEnergy=-1.000000 Imag=1 LowestFreq=-123.4\nH 0 0 0\nH 0 0 0.74\n",
        encoding="utf-8",
    )

    out = tmp_path / "output.xyz"
    args = refine.RefineOptions(
        input_file=str(inp),
        output=str(out),
        threshold=0.25,
        ewin=None,
        imag=1,
        noH=False,
        max_conformers=None,
        dedup_only=False,
        keep_all_topos=False,
        workers=1,
    )

    refine.process_xyz(args)
    text = out.read_text(encoding="utf-8")
    assert "Imag=1" in text


# ---------------------------------------------------------------------------
# P1-5: greedy_permutation_rmsd unit tests
# ---------------------------------------------------------------------------


def _elem_ids(*symbols):
    """Return an integer array of element IDs (atomic numbers) for given symbols."""
    from confflow.blocks.refine.rmsd_engine import get_element_atomic_number

    return np.array([get_element_atomic_number(s) for s in symbols])


def test_greedy_permutation_rmsd_identical():
    """Two identical structures → RMSD ≈ 0."""
    coords = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
    ids = _elem_ids("C", "C", "C")
    assert greedy_permutation_rmsd(coords, coords.copy(), ids, ids.copy()) < 1e-6


def test_greedy_permutation_rmsd_translated():
    """Pure translation: centering should make RMSD ≈ 0."""
    coords1 = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]])
    coords2 = coords1 + 5.0
    ids = _elem_ids("C", "C", "C")
    assert greedy_permutation_rmsd(coords1, coords2, ids, ids) < 1e-6


def test_greedy_permutation_rmsd_symmetry_permuted():
    """Symmetric pair: two H atoms swapped → RMSD should still be ≈ 0."""
    # O at origin, two H symmetrically placed
    coords1 = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [-1.0, 0.0, 0.0]])
    coords2 = np.array([[0.0, 0.0, 0.0], [-1.0, 0.0, 0.0], [1.0, 0.0, 0.0]])  # H's swapped
    ids = _elem_ids("O", "H", "H")
    assert greedy_permutation_rmsd(coords1, coords2, ids, ids) < 1e-6


def test_greedy_permutation_rmsd_impossible_element_match():
    """Completely different element sets → 999.9 (no valid match)."""
    coords = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    ids1 = _elem_ids("C", "C")
    ids2 = _elem_ids("N", "N")
    assert greedy_permutation_rmsd(coords, coords.copy(), ids1, ids2) == 999.9


def test_greedy_permutation_rmsd_size_mismatch():
    """n1 ≠ n2 → guard returns 999.9."""
    c1 = np.array([[0.0, 0.0, 0.0]])
    c2 = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    ids1 = _elem_ids("C")
    ids2 = _elem_ids("C", "C")
    assert greedy_permutation_rmsd(c1, c2, ids1, ids2) == 999.9


def test_greedy_permutation_rmsd_empty():
    """n=0 → guard returns 999.9."""
    empty = np.empty((0, 3))
    empty_ids = np.array([], dtype=np.int64)
    assert greedy_permutation_rmsd(empty, empty, empty_ids, empty_ids) == 999.9
