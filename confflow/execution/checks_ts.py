#!/usr/bin/env python3

"""V4 transition-state scientific checks.

``ts_mode`` tests that the lowest imaginary mode of a transition-state
optimisation is dominated by the declared reaction bonds, not by rotor or
methyl motions.  Failures carry ``check_failed`` with a stable ``reason``:
``no_imaginary_mode`` (no parsed displacement data, or unusable data),
``invalid_check_parameter``, ``bond_atoms_missing``, and
``ts_mode_not_reactive`` (overlap below ``min_overlap``).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from .checks import (
    CHECK_DEFAULTS,
    CheckContext,
    CheckOutcome,
    check_failure,
    coords_array,
    output_structure,
    strict_pairs,
)

__all__ = ["TS_MODE_CHECK_NAME", "TsModeCheck", "reaction_coordinate_overlap"]

TS_MODE_CHECK_NAME: str = "ts_mode"

#: Fallback overlap threshold when the registry default is absent.
_FALLBACK_MIN_OVERLAP: float = 0.2

#: Singular values below this are treated as linear dependence in the stretch set.
_DEPENDENCE_TOLERANCE: float = 1e-8


def _default_min_overlap() -> float:
    """Return the single-source default overlap threshold."""
    default = CHECK_DEFAULTS.get(TS_MODE_CHECK_NAME, {}).get("min_overlap", _FALLBACK_MIN_OVERLAP)
    return float(default)


def _parse_min_overlap(value: Any) -> float | None:
    """Coerce ``min_overlap`` to a float in ``[0, 1]``, or ``None``."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        return None
    return number


def _displacement_array(value: Any) -> np.ndarray | None:
    """Return the ``(N, 3)`` displacement array of an imaginary mode, or ``None``."""
    if not isinstance(value, dict):
        return None
    try:
        array = np.array(value.get("displacements"), dtype=float)
    except (TypeError, ValueError):
        return None
    if array.ndim != 2 or array.shape[1] != 3 or array.shape[0] == 0:
        return None
    if not np.all(np.isfinite(array)):
        return None
    return array


def reaction_coordinate_overlap(
    displacements: np.ndarray,
    coordinates: np.ndarray,
    pairs: list[tuple[int, int]],
) -> float | None:
    """Return the overlap of the mode ``u`` with the span of the reacting-pair stretches.

    For each pair ``(a, b)`` the unit stretch vector ``s_ab`` has ``+e`` on
    atom ``a`` and ``-e`` on atom ``b``, where ``e`` is the unit vector from
    ``a`` to ``b``.  The set ``{s_ab}`` is orthonormalised (SVD, dropping
    near-dependent directions below ``1e-8``) and the overlap is
    ``||Q^T u|| / ||u||``.  Each pair contributes its own direction, so a
    forming and a breaking bond that stretch with opposite signs do not
    cancel.  For a single pair this equals ``|u . s| / |u|``.

    Frame requirement: ``displacements`` and ``coordinates`` must be in the
    same orientation.  The check uses the parsed final geometry of the same
    log, which is the frame Gaussian and ORCA print the modes in.

    Parameters
    ----------
    displacements : np.ndarray
        ``(N, 3)`` Cartesian displacement of the imaginary mode.
    coordinates : np.ndarray
        ``(N, 3)`` output geometry in Angstrom, in the mode's frame.
    pairs : list[tuple[int, int]]
        1-based reacting atom pairs.

    Returns
    -------
    float | None
        Overlap in ``[0, 1]``; ``None`` when a pair is degenerate or out of
        range, or when the mode has zero norm.
    """
    count = coordinates.shape[0]
    if displacements.shape[0] != count:
        return None
    mode = displacements.reshape(-1)
    mode_norm = float(np.linalg.norm(mode))
    if mode_norm == 0.0:
        return None
    stretches: list[np.ndarray] = []
    for atom_a, atom_b in pairs:
        if atom_a > count or atom_b > count:
            return None
        delta = coordinates[atom_b - 1] - coordinates[atom_a - 1]
        length = float(np.linalg.norm(delta))
        if length == 0.0 or not math.isfinite(length):
            return None
        unit = delta / length
        stretch = np.zeros((count, 3))
        stretch[atom_a - 1] = unit
        stretch[atom_b - 1] = -unit
        stretches.append(stretch.reshape(-1))
    if not stretches:
        return None
    basis = np.column_stack(stretches)
    left, singular, _ = np.linalg.svd(basis, full_matrices=False)
    orthonormal = left[:, singular > _DEPENDENCE_TOLERANCE]
    projection = orthonormal.T @ mode
    return float(min(1.0, float(np.linalg.norm(projection)) / mode_norm))


class TsModeCheck:
    """Require the imaginary mode to be dominated by the declared reaction bonds."""

    @property
    def name(self) -> str:
        """Return the check name as declared in workflow documents."""
        return TS_MODE_CHECK_NAME

    @property
    def contract_version(self) -> str:
        """Return the check contract version (folded into digests)."""
        return "confflow.contract.check.ts_mode.v1"

    def run(self, context: CheckContext) -> CheckOutcome:
        """Pass iff the imaginary-mode overlap with the reaction vector is high enough."""
        pairs = strict_pairs(context.param("pairs", None))
        if pairs is None:
            return check_failure(
                context,
                "invalid_check_parameter",
                "Check parameter 'pairs' must be a non-empty list of distinct [a, b] "
                "1-based atom pairs.",
                {"parameter": "pairs"},
            )
        min_overlap = _parse_min_overlap(context.param("min_overlap", _default_min_overlap()))
        if min_overlap is None:
            return check_failure(
                context,
                "invalid_check_parameter",
                "Check parameter 'min_overlap' must be a number in [0, 1].",
                {"parameter": "min_overlap"},
            )
        mode_result = context.profile_output.results.first("imaginary_mode")
        displacements = None if mode_result is None else _displacement_array(mode_result.value)
        if mode_result is None or displacements is None:
            return check_failure(
                context,
                "no_imaginary_mode",
                "No imaginary-mode displacement data was parsed from the native output.",
            )
        frequency = float(mode_result.value["frequency_cm"])  # type: ignore[index]
        output = output_structure(context)
        coordinates = None if output is None else coords_array(output)
        if coordinates is None:
            return check_failure(
                context,
                "bond_atoms_missing",
                "Output geometry is absent or unusable; reaction pairs cannot be resolved.",
            )
        if any(max(pair) > coordinates.shape[0] for pair in pairs):
            return check_failure(
                context,
                "bond_atoms_missing",
                "A declared reaction pair does not resolve against the output geometry.",
                {"pairs": [list(pair) for pair in pairs]},
            )
        if displacements.shape[0] != coordinates.shape[0]:
            return check_failure(
                context,
                "no_imaginary_mode",
                "Imaginary-mode displacement shape does not match the output geometry.",
            )
        if not np.any(displacements):
            return check_failure(
                context,
                "no_imaginary_mode",
                "Imaginary-mode displacement vector is identically zero.",
            )
        overlap = reaction_coordinate_overlap(displacements, coordinates, pairs)
        if overlap is None:
            return check_failure(
                context,
                "bond_atoms_missing",
                "A declared reaction pair is degenerate (coincident atoms).",
                {"pairs": [list(pair) for pair in pairs]},
            )
        if overlap < min_overlap:
            return check_failure(
                context,
                "ts_mode_not_reactive",
                f"Imaginary mode ({frequency:.1f} cm^-1) overlaps the reaction "
                f"coordinate by {overlap:.3f}, below the required {min_overlap:.3f}.",
                {"overlap": overlap, "frequency_cm": frequency, "min_overlap": min_overlap},
            )
        return CheckOutcome(passed=True)
