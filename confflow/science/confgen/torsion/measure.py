#!/usr/bin/env python3

"""Dihedral measurement for ConfGen v3 torsion audit (CORE lane).

Pure NumPy geometry used by torsion realization (absolute setpoints) and
perception (measured deltas). This module adds measurement only; rotation
mechanics stay wrapped from :mod:`confflow.science.torsion`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

__all__ = ["measure_dihedral", "wrap_degrees"]


def wrap_degrees(angle: float) -> float:
    """Wrap an angle in degrees into (-180, 180]."""
    wrapped = math.fmod(float(angle) + 180.0, 360.0)
    if wrapped <= 0.0:
        wrapped += 360.0
    return wrapped - 180.0


def measure_dihedral(
    coords: Sequence[Sequence[float]] | np.ndarray, a: int, b: int, c: int, d: int
) -> float:
    """Return the dihedral angle for atoms a-b-c-d in degrees, (-180, 180].

    Atan2 formulation over the (a,b,c)/(b,c,d) plane normals. Raises
    :class:`ValueError` on degenerate (collinear/zero-length) frames.
    """
    points = np.asarray(coords, dtype=np.float64)
    try:
        pa, pb, pc, pd = points[a], points[b], points[c], points[d]
    except IndexError as exc:
        raise ValueError(f"dihedral frame {(a, b, c, d)} out of range") from exc
    axis = pc - pb
    axis_norm = float(np.linalg.norm(axis))
    if axis_norm < 1e-12:
        raise ValueError("dihedral central bond is degenerate")
    unit = axis / axis_norm
    v0 = pa - pb
    v1 = pd - pc
    v = v0 - np.dot(v0, unit) * unit
    w = v1 - np.dot(v1, unit) * unit
    if float(np.linalg.norm(v)) < 1e-12 or float(np.linalg.norm(w)) < 1e-12:
        raise ValueError("dihedral terminal frame is degenerate")
    x = float(np.dot(v, w))
    y = float(np.dot(np.cross(unit, v), w))
    return math.degrees(math.atan2(y, x))
