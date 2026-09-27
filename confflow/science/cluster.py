#!/usr/bin/env python3

"""RMSD comparison science for V4 transform dedup (pure numeric).

Extracted from the pure subset of ``confflow.blocks.refine`` with no
import of the legacy processor, pools, XYZ I/O, or CLI:

- :func:`kabsch_rmsd` is the reflection-free Kabsch formulation of
  ``rmsd_engine.kabsch_rmsd`` / ``rmsd_engine.fast_rmsd``;
- the comparison rule enforced by callers follows
  ``rmsd_engine.compare_frames``: RMSD is only evaluated under a legal
  element/edge-preserving mapping.  This module evaluates the identity
  mapping; pairs whose atom order differs are reported, never collapsed.

Dependencies: NumPy only.
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "kabsch_rmsd",
]


def kabsch_rmsd(first: np.ndarray, second: np.ndarray) -> float:
    """Return the reflection-free Kabsch RMSD of two ``(N, 3)`` arrays.

    Both sets are centered; with ``H = Y.T @ X`` and ``U, S, Vt =
    svd(H)`` the optimal proper rotation is ``R = U @ D @ Vt`` (``D``
    fixes the determinant sign) and the RMSD is over ``X - Y @ R``.
    """
    x = np.asarray(first, dtype=np.float64)
    y = np.asarray(second, dtype=np.float64)
    x_centered = x - x.mean(axis=0)
    y_centered = y - y.mean(axis=0)
    left, _singular, right_transposed = np.linalg.svd(y_centered.T @ x_centered)
    correction = np.eye(3)
    correction[-1, -1] = 1.0 if float(np.linalg.det(left @ right_transposed)) >= 0.0 else -1.0
    aligned = y_centered @ (left @ correction @ right_transposed)
    return float(np.sqrt(np.mean(np.sum((x_centered - aligned) ** 2, axis=1))))
