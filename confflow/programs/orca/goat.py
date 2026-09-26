#!/usr/bin/env python3

"""ORCA GOAT conformer-search input rendering for the V4 program adapter.

Pure rendering and shape gates for the ``%goat`` block.  This module owns
V4-native copies of GOAT vocabulary handling and never imports the legacy
calc runtime.

Honest boundary: the repository-wide legacy audit found zero legacy GOAT
syntax, so no historical key spellings are ported here.  The supported keys
are an explicitly allowlisted crafted subset with the dialect contract
documented below; anything else fails closed with ``native_input_error``.
Key semantics are stated as this module's contract (units, types, ranges),
not as claims about any particular ORCA release's defaults.

Allowlisted ``%goat`` keys (one line each):

- ``MaxIter``: maximum GOAT global-optimization iterations (int >= 1).
- ``MaxConformers``: maximum conformers retained in the ensemble (int >= 1).
- ``EnergyWindow``: keep conformers within this window above the minimum,
  in kcal/mol (finite float > 0; ints are accepted as exact values).
- ``Seed``: explicit RNG seed for GOAT sampling (int >= 0), so a resume can
  never silently re-randomize a run (see ``confflow.domain.stochastic``).

Rendering contract: ``render_goat_blocks`` emits ``%goat ... end`` text with
keys sorted alphabetically, two-space indents, and a trailing newline, which
matches the house ``%block`` style used by ``rendering.format_orca_blocks``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "GOAT_BLOCK_KEYS",
    "goat_artifact_names",
    "render_goat_blocks",
    "validate_goat_inputs",
]

#: Strict native vocabulary accepted in ``native["goat"]``.
GOAT_BLOCK_KEYS: frozenset[str] = frozenset({"MaxIter", "MaxConformers", "EnergyWindow", "Seed"})

_INT_KEYS: frozenset[str] = frozenset({"MaxIter", "MaxConformers", "Seed"})


def _check_int_key(key: str, value: Any) -> int:
    """Validate an integer-valued ``%goat`` key.

    Parameters
    ----------
    key : str
        Key name used in error messages.
    value : Any
        Candidate value from the ``goat`` mapping.

    Returns
    -------
    int
        The validated value.

    Raises
    ------
    ValueError
        Raised when the value is not an integer in range (bools rejected).
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"native_input_error: ORCA '%goat' key {key!r} must be an integer, " f"got {value!r}"
        )
    minimum = 0 if key == "Seed" else 1
    if value < minimum:
        raise ValueError(
            f"native_input_error: ORCA '%goat' key {key!r} must be >= {minimum}, " f"got {value!r}"
        )
    return value


def _check_energy_window(value: Any) -> int | float:
    """Validate the ``EnergyWindow`` value in kcal/mol.

    Parameters
    ----------
    value : Any
        Candidate value from the ``goat`` mapping.

    Returns
    -------
    int | float
        The validated value, unchanged.

    Raises
    ------
    ValueError
        Raised when the value is not a finite number greater than zero
        (bools rejected).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            "native_input_error: ORCA '%goat' key 'EnergyWindow' must be a "
            f"number of kcal/mol, got {value!r}"
        )
    if not math.isfinite(float(value)) or float(value) <= 0.0:
        raise ValueError(
            "native_input_error: ORCA '%goat' key 'EnergyWindow' must be a "
            f"finite number > 0, got {value!r}"
        )
    return value


def _format_value(value: int | float) -> str:
    """Format a validated ``%goat`` value deterministically.

    Parameters
    ----------
    value : int | float
        Validated key value.

    Returns
    -------
    str
        Plain ``str`` for ints, ``repr`` for floats (full precision).
    """
    if isinstance(value, float):
        return repr(value)
    return str(value)


def render_goat_blocks(native: Mapping[str, Any]) -> str:
    """Render the ``%goat`` block from native options.

    Parameters
    ----------
    native : Mapping[str, Any]
        Native option mapping from resolved calculation inputs; must carry a
        ``"goat"`` entry holding a mapping of allowlisted ``%goat`` keys.

    Returns
    -------
    str
        Rendered ``%goat ... end`` text with keys sorted alphabetically and
        a trailing newline.

    Raises
    ------
    ValueError
        Raised when ``native["goat"]`` is absent or not a mapping, when an
        unknown ``%goat`` key appears, or when a value has the wrong type
        or is out of range.
    """
    if not isinstance(native, Mapping):
        raise ValueError(
            "native_input_error: GOAT requires native['goat'] mapping, " f"got native={native!r}"
        )
    goat = native.get("goat")
    if not isinstance(goat, Mapping):
        raise ValueError("native_input_error: GOAT requires native['goat'] mapping")
    unknown = sorted(key for key in goat if key not in GOAT_BLOCK_KEYS)
    if unknown:
        raise ValueError(
            "native_input_error: unknown ORCA '%goat' key(s): "
            + ", ".join(repr(key) for key in unknown)
        )
    rendered: dict[str, str] = {}
    for key in goat:
        if key in _INT_KEYS:
            rendered[key] = _format_value(_check_int_key(key, goat[key]))
        elif key == "EnergyWindow":
            rendered[key] = _format_value(_check_energy_window(goat[key]))
    lines = ["%goat"]
    for key in sorted(rendered):
        lines.append(f"  {key} {rendered[key]}")
    lines.append("end")
    return "\n".join(lines) + "\n"


def validate_goat_inputs(
    atoms: Sequence[str],
    coords: Sequence[Sequence[float]],
    charge: Any,
    multiplicity: Any,
) -> None:
    """Validate GOAT input shapes; charge/multiplicity belong to the executor.

    Parameters
    ----------
    atoms : Sequence[str]
        Element symbols in atom order; must be non-empty.
    coords : Sequence[Sequence[float]]
        Cartesian coordinates in Angstrom, one finite ``(x, y, z)`` triple
        per atom.
    charge : Any
        Total charge; accepted unchecked (the executor owns charge and
        multiplicity resolution, this gate only checks structure shape).
    multiplicity : Any
        Spin multiplicity; accepted unchecked, same ownership as ``charge``.

    Returns
    -------
    None

    Raises
    ------
    ValueError
        Raised when the structure is empty, when the coordinate count does
        not match the atom count, or when a coordinate is not a finite
        ``(x, y, z)`` triple.
    """
    symbols = tuple(atoms)
    if not symbols:
        raise ValueError("native_input_error: GOAT requires a non-empty structure")
    for symbol in symbols:
        if not isinstance(symbol, str) or not symbol:
            raise ValueError(
                f"native_input_error: GOAT atom symbols must be non-empty strings, "
                f"got {symbol!r}"
            )
    points = tuple(tuple(point) for point in coords)
    if len(points) != len(symbols):
        raise ValueError(
            "native_input_error: GOAT coordinate count "
            f"({len(points)}) != atom count ({len(symbols)})"
        )
    for point in points:
        if len(point) != 3:
            raise ValueError(
                f"native_input_error: GOAT coordinates must be (x, y, z) triples, " f"got {point!r}"
            )
        for value in point:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    "native_input_error: GOAT coordinates must be finite numbers, " f"got {point!r}"
                )
            if not math.isfinite(float(value)):
                raise ValueError(
                    "native_input_error: GOAT coordinates must be finite numbers, " f"got {point!r}"
                )


def goat_artifact_names(job: str) -> dict[str, str]:
    """Return deterministic GOAT artifact file names for a job.

    Parameters
    ----------
    job : str
        Sanitized job name (e.g. from ``rendering.sanitize_job_name``); must
        be a non-empty string carrying no directory components.

    Returns
    -------
    dict[str, str]
        ``{"trajectory_xyz": "<job>.goat.xyz", "log": "<job>.goat.out"}``:
        the conformer-trajectory XYZ file and the GOAT log file.

    Raises
    ------
    ValueError
        Raised when ``job`` is empty or carries a directory component.
    """
    if not isinstance(job, str) or not job:
        raise ValueError("native_input_error: GOAT job name must be a non-empty string")
    if "/" in job or "\\" in job or job in (".", ".."):
        raise ValueError(
            f"native_input_error: GOAT job name must carry no directories, got {job!r}"
        )
    return {"trajectory_xyz": f"{job}.goat.xyz", "log": f"{job}.goat.out"}
