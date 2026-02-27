#!/usr/bin/env python3

"""Collision detection core module for ConfGen.

Isolates Numba JIT-accelerated clash detection logic for
independent testing and reuse.
"""

from __future__ import annotations

import numpy as np

from ...core.data import GV_COVALENT_RADII
from ...core.utils import get_numba_jit

numba = get_numba_jit("confflow.confgen")

__all__ = [
    "GV_RADII_ARRAY",
    "check_clash_core",
]

# Build NumPy array for high-performance computation
GV_RADII_ARRAY = np.zeros(120, dtype=np.float64)
for _i, _r in enumerate(GV_COVALENT_RADII):
    GV_RADII_ARRAY[_i] = _r
for _i in range(112, 120):
    GV_RADII_ARRAY[_i] = 1.50


@numba.njit(cache=True)
def check_clash_core(atom_numbers, coords, clash_threshold, topo_dist_matrix, radii_array):
    """Check for severe atomic clashes.

    Atoms are considered clashing if
    distance < (R1 + R2) * clash_threshold.

    Parameters
    ----------
    atom_numbers : ndarray
        Atomic numbers for each atom.
    coords : ndarray
        Atomic coordinates, shape (N, 3).
    clash_threshold : float
        Scaling factor for the sum of covalent radii.
    topo_dist_matrix : ndarray
        Topological distance matrix.
    radii_array : ndarray
        Covalent radii indexed by atomic number.

    Returns
    -------
    bool
        True if a clash is detected.
    """
    num_atoms = len(atom_numbers)
    radii = np.empty(num_atoms, dtype=np.float64)
    for i in range(num_atoms):
        radii[i] = radii_array[atom_numbers[i]]

    # Topological distance filter: ignore pairs within 1-4 bonds (non-bonded interaction standard)
    ignore_hops = 3

    for i in range(num_atoms):
        for j in range(i + 1, num_atoms):
            # 1. Topological filter
            if topo_dist_matrix[i, j] <= ignore_hops:
                continue

            # 2. Distance calculation
            dist_sq = (
                (coords[i, 0] - coords[j, 0]) ** 2
                + (coords[i, 1] - coords[j, 1]) ** 2
                + (coords[i, 2] - coords[j, 2]) ** 2
            )

            # 3. Soft-sphere clash criterion
            sum_radii = radii[i] + radii[j]
            limit = sum_radii * clash_threshold

            if dist_sq < (limit * limit):
                return True  # Clash detected, discard

    return False  # No clash, passed
