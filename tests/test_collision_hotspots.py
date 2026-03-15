#!/usr/bin/env python3
"""Hotspot tests for confgen.collision with fake numba execution."""

from __future__ import annotations

import importlib
from unittest.mock import patch

import numpy as np

import confflow.blocks.confgen.collision as collision


class _FakeNumba:
    __name__ = "FakeNumba"

    @staticmethod
    def njit(*args, **kwargs):
        del kwargs

        def decorator(func):
            return func

        return decorator if not args else args[0]


def _reload_collision_with_fake_numba():
    with patch("confflow.core.utils.get_numba_jit", return_value=_FakeNumba()):
        return importlib.reload(collision)


def test_collision_module_fake_numba_executes_python_body():
    fake_collision = _reload_collision_with_fake_numba()

    try:
        assert fake_collision.numba.__name__ == "FakeNumba"
        assert fake_collision.GV_RADII_ARRAY[6] > 0.0
        assert np.allclose(fake_collision.GV_RADII_ARRAY[112:120], 1.50)

        atom_numbers = np.array([6, 6], dtype=np.int64)
        topo = np.array([[0, 10], [10, 0]], dtype=np.int64)

        coords_clash = np.array([[0.0, 0.0, 0.0], [0.8, 0.0, 0.0]], dtype=np.float64)
        assert (
            fake_collision.check_clash_core(
                atom_numbers, coords_clash, 0.7, topo, fake_collision.GV_RADII_ARRAY
            )
            is True
        )

        coords_ok = np.array([[0.0, 0.0, 0.0], [5.0, 0.0, 0.0]], dtype=np.float64)
        assert (
            fake_collision.check_clash_core(
                atom_numbers, coords_ok, 0.7, topo, fake_collision.GV_RADII_ARRAY
            )
            is False
        )
    finally:
        importlib.reload(collision)


def test_collision_fake_numba_topological_filter_and_single_atom():
    fake_collision = _reload_collision_with_fake_numba()

    try:
        single_atom = np.array([6], dtype=np.int64)
        single_coords = np.array([[0.0, 0.0, 0.0]], dtype=np.float64)
        single_topo = np.array([[0]], dtype=np.int64)
        assert (
            fake_collision.check_clash_core(
                single_atom, single_coords, 0.7, single_topo, fake_collision.GV_RADII_ARRAY
            )
            is False
        )

        atom_numbers = np.array([6, 6, 6], dtype=np.int64)
        coords = np.array([[0.0, 0.0, 0.0], [0.4, 0.0, 0.0], [0.8, 0.0, 0.0]], dtype=np.float64)
        topo = np.array([[0, 1, 2], [1, 0, 1], [2, 1, 0]], dtype=np.int64)
        assert (
            fake_collision.check_clash_core(
                atom_numbers, coords, 0.7, topo, fake_collision.GV_RADII_ARRAY
            )
            is False
        )
    finally:
        importlib.reload(collision)
