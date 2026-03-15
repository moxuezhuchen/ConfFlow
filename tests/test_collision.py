#!/usr/bin/env python3

"""Unit tests for check_clash_core (P2-5).

Tests the Numba JIT-compiled clash detection logic directly, covering the cases:
- No clash (atoms far apart)
- 1-4 topological filter (close atoms within ≤3 bond hops are ignored)
- Heavy-heavy clash (C-C close, outside topological filter)
- Hydrogen pair clash (H-H close, outside topological filter)
"""

from __future__ import annotations

import numpy as np

from confflow.blocks.confgen.collision import GV_RADII_ARRAY, check_clash_core


def _topo(n: int, dist: int = 10) -> np.ndarray:
    """Build an n×n topo-distance matrix with off-diagonal = dist."""
    m = np.full((n, n), dist, dtype=np.float64)
    np.fill_diagonal(m, 0)
    return m


def test_check_clash_core_no_clash():
    """Atoms 10 Å apart → no clash regardless of element."""
    atom_numbers = np.array([1, 1], dtype=np.int64)  # H, H
    coords = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]], dtype=np.float64)
    topo = _topo(2, dist=10)
    assert check_clash_core(atom_numbers, coords, 0.7, topo, GV_RADII_ARRAY) is False


def test_check_clash_core_topological_filter_1_4():
    """C-C pair at 0.5 Å but within 2 bond hops → 1-4 filter suppresses clash."""
    # C covalent radius = 0.77 Å; 2*0.77*0.7 = 1.078 Å > 0.5 Å would be a clash,
    # but topo_dist = 2 ≤ 3 hops so the pair is skipped entirely.
    atom_numbers = np.array([6, 6], dtype=np.int64)  # C, C
    coords = np.array([[0.0, 0.0, 0.0], [0.5, 0.0, 0.0]], dtype=np.float64)
    topo = _topo(2, dist=2)  # 2 hops apart — within 1-4 filter
    assert check_clash_core(atom_numbers, coords, 0.7, topo, GV_RADII_ARRAY) is False


def test_check_clash_core_heavy_heavy_clash():
    """C-C pair at 0.5 Å, topologically distant → clash detected.

    limit = 2 * 0.77 * 0.7 = 1.078 Å; 0.5 Å < 1.078 Å → True.
    """
    atom_numbers = np.array([6, 6], dtype=np.int64)  # C, C
    coords = np.array([[0.0, 0.0, 0.0], [0.5, 0.0, 0.0]], dtype=np.float64)
    topo = _topo(2, dist=10)  # well separated topologically
    assert check_clash_core(atom_numbers, coords, 0.7, topo, GV_RADII_ARRAY) is True


def test_check_clash_core_hydrogen_pair_clash():
    """H-H pair at 0.3 Å, topologically distant → clash detected.

    limit = 2 * 0.30 * 0.7 = 0.42 Å; 0.3 Å < 0.42 Å → True.
    """
    atom_numbers = np.array([1, 1], dtype=np.int64)  # H, H
    coords = np.array([[0.0, 0.0, 0.0], [0.3, 0.0, 0.0]], dtype=np.float64)
    topo = _topo(2, dist=10)
    assert check_clash_core(atom_numbers, coords, 0.7, topo, GV_RADII_ARRAY) is True


def test_check_clash_core_three_atoms_only_distant_pair_clashes():
    """Three atoms: middle pair filtered by topo, outer pair clashes."""
    # Atom 0 (C) and Atom 2 (C) are far topologically but close in space.
    # Atom 0-1 and Atom 1-2 are within 1-4 filter.
    atom_numbers = np.array([6, 6, 6], dtype=np.int64)
    coords = np.array([[0.0, 0.0, 0.0], [0.5, 0.0, 0.0], [0.9, 0.0, 0.0]], dtype=np.float64)
    topo = np.array(
        [[0, 1, 10], [1, 0, 1], [10, 1, 0]], dtype=np.float64
    )  # 0-1 bonded, 1-2 bonded, 0-2 distant topo
    # 0-2 distance = 0.9 Å; limit = 2*0.77*0.7 = 1.078 → clash
    assert check_clash_core(atom_numbers, coords, 0.7, topo, GV_RADII_ARRAY) is True
