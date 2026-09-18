#!/usr/bin/env python3

"""Post-processing parsing and analysis (TS bond, RMSD helpers, etc.)."""

from __future__ import annotations

import re
from typing import Any

import numpy as np

from ..core import io as io_xyz
from ..shared.defaults import DEFAULT_TS_BOND_DRIFT_THRESHOLD, DEFAULT_TS_RMSD_THRESHOLD

__all__ = [
    "FREQUENCY_NOISE_FLOOR_CM",
    "analyze_vibrational_frequencies",
    "validate_ts_bond_drift",
    "validate_ts_rmsd",
    "is_rescue_enabled",
]

#: Modes within this many cm⁻¹ of zero are numerical noise (near-zero translations
#: / rotations in ORCA, or soft modes) and must not be counted as imaginary.
FREQUENCY_NOISE_FLOOR_CM = 10.0


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


def analyze_vibrational_frequencies(
    freqs: list[float],
    *,
    skip_rigid_modes: int = 0,
    noise_floor_cm: float = FREQUENCY_NOISE_FLOOR_CM,
) -> tuple[int, float | None]:
    """Count imaginary modes and report the lowest true vibrational frequency.

    ``skip_rigid_modes`` drops the leading translations/rotations (ORCA lists
    them explicitly; Gaussian does not).  Modes within ``noise_floor_cm`` of
    zero are treated as numerical noise, so both parsers classify modes with
    the same rule and a near-zero rigid mode is never mistaken for an
    imaginary frequency.
    """
    modes: list[float] = []
    for value in freqs[skip_rigid_modes:]:
        try:
            freq = float(value)
        except (TypeError, ValueError):
            continue
        if not np.isfinite(freq) or abs(freq) <= noise_floor_cm:
            continue
        modes.append(freq)
    num_imag = sum(1 for freq in modes if freq < 0.0)
    lowest = min(modes) if modes else None
    return num_imag, lowest


def _kabsch_aligned_rmsd(a: np.ndarray, b: np.ndarray) -> float | None:
    """Proper (reflection-free) Kabsch-aligned RMSD between two (N, 3) arrays."""
    if a.shape != b.shape or a.shape[0] == 0:
        return None
    if not np.all(np.isfinite(a)) or not np.all(np.isfinite(b)):
        return None
    a_centered = a - a.mean(axis=0)
    b_centered = b - b.mean(axis=0)
    # Rotate the moving set ``a`` onto the fixed set ``b`` (same formulation as
    # the refine RMSD engine).
    h = a_centered.T @ b_centered
    u, _s, vt = np.linalg.svd(h)
    d = np.eye(3)
    d[-1, -1] = 1.0 if np.linalg.det(u @ vt) >= 0.0 else -1.0
    a_aligned = a_centered @ (u @ d @ vt)
    return float(np.sqrt(np.mean(np.sum((b_centered - a_aligned) ** 2, axis=1))))


def validate_ts_rmsd(
    initial_coords: list[str],
    final_coords: list[str],
    threshold: float | None = None,
    *,
    context: str = "TS",
) -> str | None:
    """Check the Kabsch-aligned all-atom RMSD of a TS optimization.

    The displacement is measured between the optimized structure and the
    structure the optimization started from (the user TS guess for a normal TS
    task, the selected scan candidate for rescue reoptimization).

    Fails closed: returns ``None`` only when the RMSD is computable and within
    ``threshold``; unparseable coordinates, mismatched atom counts, or
    non-finite coordinates return an explicit error message.
    """
    if threshold is None:
        threshold = DEFAULT_TS_RMSD_THRESHOLD
    initial = _coords_array_from_xyz_lines(initial_coords)
    final = _coords_array_from_xyz_lines(final_coords)
    if initial is None or final is None:
        return (
            f"{context} geometry criterion failed: coordinates could not be "
            "parsed to compute the aligned RMSD"
        )
    if initial.shape != final.shape:
        return (
            f"{context} geometry criterion failed: atom count mismatch "
            f"(initial {initial.shape[0]}, final {final.shape[0]})"
        )
    rmsd = _kabsch_aligned_rmsd(initial, final)
    if rmsd is None:
        return (
            f"{context} geometry criterion failed: aligned RMSD could not be "
            "computed (non-finite coordinates)"
        )
    if rmsd > threshold:
        return (
            f"{context} geometry criterion failed: aligned RMSD {rmsd:.3f} Å "
            f"exceeds threshold {threshold:.3f} Å"
        )
    return None
