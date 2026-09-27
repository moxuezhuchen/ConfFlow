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

Seed authority (verified against the official ORCA 6.1 manual and the
installed ORCA 6.1.1 binary): the native ``%goat`` key ``RANDOMSEED``
is a boolean randomization switch, NOT an integer RNG seed.  The
manual's ``%goat`` keyword table (Table 4.9) documents
``RANDOMSEED`` with default ``true`` as "set it to false to have a
deterministic GOAT run. since the geometry optimization can change due
to numerical differences it might not be fully deterministic in some
cases."  No numeric stream-selection mechanism exists in ORCA 6.1.
The binary's input parser also accepts integers, booleans, and even
floats for this key (unknown keys fail fast with "Unknown
identifier"; non-numeric values fail with "Invalid assignment"), but
parser acceptance is not proof of RNG-seed semantics: live
butane/hexane/decane XTB probes show no stream-selection behavior:
identical ensembles across ``RANDOMSEED false/true/7/42`` on converged
searches, and bit-identical repeat runs (including per-global-iteration
files) under ``RANDOMSEED false`` on a 2-global-iteration decane search.
Nothing demonstrates that distinct integers select distinct streams.
The single stochastic authority is the typed step seed: the program
adapter requires it (fail-closed when missing), folds it into the
step semantic digest and the remote envelope so distinct seeds never
share identity, and renders the native flag deterministically as
``RANDOMSEED false``.  Any user-supplied ``RANDOMSEED`` is a second
authority (compile-time validation rejects the key before rendering).
The invented ``Seed`` key never existed natively and is rejected as
an unknown key.  Only same-input reproducibility in deterministic
mode is claimed, best-effort per the manual's numerical caveat;
distinct step seeds share native ``.inp`` bytes by design (ORCA
exposes no per-stream selection) and differ only in digest/envelope
identity.

Allowlisted ``%goat`` keys (one line each):

- ``MaxIter``: maximum GOAT geometry-optimization iterations per worker
  (int >= 1; verified against the installed ORCA 6.1.1 binary and the
  official 6.1 manual).
- ``RANDOMSEED``: the native boolean randomization switch, rendered
  exclusively by the program adapter as ``false`` (deterministic mode
  per the official manual) whenever a GOAT step carries its required
  typed step seed.  ConfFlow accepts only booleans here — stricter
  than the native parser, which also tolerates numbers — because the
  manual defines no numeric seed semantics.

Rendering contract: ``render_goat_blocks`` emits ``%goat ... end`` text with
keys sorted alphabetically, two-space indents, and a trailing newline, which
matches the house ``%block`` style used by ``rendering.format_orca_blocks``.

Non-goals (fail closed, never rendered): ``MaxConformers`` and
``EnergyWindow`` are NOT native ORCA 6.1 vocabulary — the installed
binary rejects both with "Unknown identifier" — so they are rejected
as unknown keys.  (The native energy-window control is ``MAXEN``; it
is not allowlisted: allowlist growth is forbidden, so ensemble
energy-window control is an explicit capability gap.)
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
#:
#: ``MaxIter`` matches the official ``MAXITER`` key (block keywords are
#: case-insensitive; verified against the installed ORCA 6.1.1 binary).
#: ``RANDOMSEED`` is the native boolean randomization switch per the
#: official ORCA 6.1 manual (``%goat`` Table 4.9: default ``true``,
#: "set it to false to have a deterministic GOAT run").  The
#: installed binary's parser additionally tolerates integers
#: (including 0 and negatives) and floats for this key — unknown
#: keys fail fast with "Unknown identifier in GOAT block" and
#: non-numeric values with "Invalid assignment in GOAT block" — but
#: parser tolerance is not RNG-seed semantics, and ConfFlow never
#: renders numbers: only booleans are accepted (stricter than native,
#: never looser).  The invented ``Seed`` key never existed natively
#: and is rejected, as are the invented ``MaxConformers`` and
#: ``EnergyWindow`` keys (both fail with "Unknown identifier" on the
#: installed binary).
GOAT_BLOCK_KEYS: frozenset[str] = frozenset({"MaxIter", "RANDOMSEED"})

_INT_KEYS: frozenset[str] = frozenset({"MaxIter"})

#: Boolean-valued ``%goat`` keys (native randomization switches).
_BOOL_KEYS: frozenset[str] = frozenset({"RANDOMSEED"})


def _check_bool_key(key: str, value: Any) -> bool:
    """Validate a boolean ``%goat`` switch (strict: only ``bool``).

    Unlike count keys, switches carry no numeric range: only actual
    booleans are accepted.  Integers such as ``7`` or ``0`` parse on
    the installed binary but carry no documented seed semantics, so
    they are rejected fail-closed here.
    """
    if not isinstance(value, bool):
        raise ValueError(
            f"native_input_error: ORCA '%goat' key {key!r} must be a boolean, " f"got {value!r}"
        )
    return value


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
    if value < 1:
        raise ValueError(
            f"native_input_error: ORCA '%goat' key {key!r} must be >= 1, " f"got {value!r}"
        )
    return value


def _format_value(value: int | float | bool) -> str:
    """Format a validated ``%goat`` value deterministically.

    Parameters
    ----------
    value : int | float | bool
        Validated key value.

    Returns
    -------
    str
        Lowercase ``true``/``false`` for booleans (ORCA canonical),
        plain ``str`` for ints, ``repr`` for floats (full precision).
    """
    if isinstance(value, bool):
        return "true" if value else "false"
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
        elif key in _BOOL_KEYS:
            rendered[key] = _format_value(_check_bool_key(key, goat[key]))
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
