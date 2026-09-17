"""Integration checks for the optional Numba acceleration extra."""

from __future__ import annotations

import numpy as np
import pytest


def _topology_matrix(distance: int) -> np.ndarray:
    matrix = np.full((2, 2), distance, dtype=np.float64)
    np.fill_diagonal(matrix, 0.0)
    return matrix


def test_collision_kernel_is_jitted_and_preserves_known_clash_results() -> None:
    pytest.importorskip(
        "numba", reason="install ConfFlow's optional speed extra to exercise JIT kernels"
    )
    from numba.core.registry import CPUDispatcher

    from confflow.blocks.confgen.collision import GV_RADII_ARRAY, check_clash_core

    assert isinstance(check_clash_core, CPUDispatcher)

    atom_numbers = np.array([6, 6], dtype=np.int64)
    close_coordinates = np.array([[0.0, 0.0, 0.0], [0.5, 0.0, 0.0]], dtype=np.float64)

    # A topologically distant C-C pair clashes; the same geometry is ignored
    # when its bond distance is inside the existing 1-4 filter.
    assert (
        check_clash_core(atom_numbers, close_coordinates, 0.7, _topology_matrix(10), GV_RADII_ARRAY)
        is True
    )
    assert (
        check_clash_core(atom_numbers, close_coordinates, 0.7, _topology_matrix(2), GV_RADII_ARRAY)
        is False
    )

    assert check_clash_core.nopython_signatures, "collision kernel did not compile in nopython mode"


def test_rmsd_kernel_is_jitted_and_matches_numpy_reference() -> None:
    pytest.importorskip(
        "numba", reason="install ConfFlow's optional speed extra to exercise JIT kernels"
    )
    from numba.core.registry import CPUDispatcher

    from confflow.blocks.refine.rmsd_engine import fast_rmsd, kabsch_rmsd

    assert isinstance(fast_rmsd, CPUDispatcher)

    fixed = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.1, -0.2],
            [0.3, 2.0, 0.4],
            [-0.6, 0.2, 3.0],
            [1.2, -1.4, 0.7],
        ],
        dtype=np.float64,
    )
    angle = 0.7
    rotation = np.array(
        [
            [np.cos(angle), -np.sin(angle), 0.0],
            [np.sin(angle), np.cos(angle), 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    moving = fixed @ rotation + np.array([5.0, 6.0, 7.0])
    moving[-1, 0] += 0.35

    compiled_result = float(fast_rmsd(fixed, moving))
    numpy_result = kabsch_rmsd(fixed, moving)

    assert numpy_result == pytest.approx(0.09834571865535, abs=1e-8)
    assert compiled_result == pytest.approx(numpy_result, abs=1e-8)
    assert fast_rmsd.nopython_signatures, "RMSD kernel did not compile in nopython mode"
