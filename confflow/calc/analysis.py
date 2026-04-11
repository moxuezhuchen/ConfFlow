#!/usr/bin/env python3

"""Post-processing parsing and analysis (TS bond, RMSD helpers, etc.)."""

from __future__ import annotations

import re
from typing import Any

import numpy as np

from ..core import io as io_xyz
from ..shared.defaults import DEFAULT_TS_BOND_DRIFT_THRESHOLD

__all__ = [
    "validate_ts_bond_drift",
    "is_rescue_enabled",
]


def _keyword_requests_freq(config: dict) -> bool:
    """Check whether the keyword explicitly requests a frequency calculation."""
    kw = str(config.get("keyword", "") or "")
    if not kw.strip():
        return False
    return re.search(r"(?i)\bfreq\b", kw) is not None


def _parse_ts_bond_atoms(val: Any) -> tuple[int, int] | None:
    """Parse the TS bond-forming/breaking atom pair (1-based indices)."""
    if val is None:
        return None
    if isinstance(val, (list, tuple)):
        nums: list[int] = []
        for x in val:
            try:
                nums.append(int(x))
            except (ValueError, TypeError):
                continue
    else:
        nums = []
        for m in re.findall(r"\d+", str(val)):
            try:
                nums.append(int(m))
            except (ValueError, TypeError):
                continue
    if len(nums) < 2:
        return None
    a, b = nums[0], nums[1]
    if a <= 0 or b <= 0 or a == b:
        return None
    return a, b


def _bond_length_from_xyz_lines(coords_lines: list[str], a1: int, a2: int) -> float | None:
    """Compute the distance between two atoms from XYZ coordinate lines (Å)."""
    return io_xyz.calculate_bond_length(coords_lines, a1, a2)


def validate_ts_bond_drift(
    initial_coords: list[str],
    final_coords: list[str],
    a1: int,
    a2: int,
    threshold: float | None = None,
    *,
    context: str = "TS",
) -> str | None:
    """Validate whether the critical bond length drift in a TS task exceeds the threshold.

    Parameters
    ----------
    initial_coords : list[str]
        Coordinate lines of the input structure.
    final_coords : list[str]
        Coordinate lines of the output structure.
    a1 : int
        First atom index of the TS bond pair (1-based).
    a2 : int
        Second atom index of the TS bond pair (1-based).
    threshold : float or None, optional
        Drift threshold in angstroms. Defaults to ``DEFAULT_TS_BOND_DRIFT_THRESHOLD``.
    context : str, optional
        Context label used as a prefix in error messages.

    Returns
    -------
    str or None
        None if the check passes; otherwise an error message string.
    """
    if threshold is None:
        threshold = DEFAULT_TS_BOND_DRIFT_THRESHOLD
    r_initial = _bond_length_from_xyz_lines(initial_coords, a1, a2)
    r_final = _bond_length_from_xyz_lines(final_coords, a1, a2)
    if r_initial is None or r_final is None:
        return None
    d_r = abs(r_final - r_initial)
    if d_r > threshold:
        return (
            f"{context} geometry criterion failed: critical bond drift |ΔR|={d_r:.3f} Å exceeds threshold {threshold:.3f} Å "
            f"(R_initial={r_initial:.3f} Å, R_final={r_final:.3f} Å, TSAtoms={a1},{a2})"
        )
    return None


def is_rescue_enabled(cfg: dict) -> bool:
    """Check whether ts_rescue_scan is enabled."""
    return str(cfg.get("ts_rescue_scan", "false")).lower() == "true"


def _coords_array_from_xyz_lines(coords_lines: list[str]) -> np.ndarray | None:
    """Parse XYZ coordinate lines into an (N, 3) numpy array."""
    if not coords_lines:
        return None
    try:
        coords: list[list[float]] = []
        for line in coords_lines:
            # Skip empty lines or None
            if line is None or not isinstance(line, str):
                return None
            parts = line.split()
            xyz: list[float] = []
            for tok in reversed(parts):
                try:
                    xyz.append(float(tok))
                except (ValueError, TypeError):
                    continue
                if len(xyz) == 3:
                    break
            if len(xyz) != 3:
                return None
            z, y, x = xyz  # reversed
            coords.append([x, y, z])
        return np.array(coords, dtype=float)  # type: ignore[no-any-return]
    except (ValueError, TypeError, AttributeError):
        # Numeric conversion failed or type error
        return None
