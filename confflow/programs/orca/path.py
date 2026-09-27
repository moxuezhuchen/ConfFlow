#!/usr/bin/env python3

"""ORCA reaction-path (IRC) helpers for ConfFlow Workflow V4.

This module renders ``%geom`` IRC modifiers and parses IRC endpoints from
real ORCA 6.1 output, verified against an installed-binary HCN
isomerization IRC (HF-3c, both directions, ``MaxIter`` truncation): the
log carries asterisk-framed ``FORWARD IRC`` / ``BACKWARD IRC`` sections
with ``Iteration / E(Eh)`` tables, and endpoint geometries live in
``<base>_IRC_F.xyz`` / ``<base>_IRC_B.xyz`` files (standard XYZ with a
``Coordinates from ORCA-job <base> E <energy>`` comment).  Direction
comes from explicit section banners only and is never inferred from
order.  ORCA's ``BACKWARD`` direction maps to the canonical ``reverse``
endpoint direction (recorded in native metadata); Gaussian's
``REVERSE`` maps identically, so downstream identity is program
independent.  It owns V4-native copies of small parsing idioms (it never
imports the legacy calc runtime, the program adapter, or the sibling
rendering/parsing helpers) and imports only
:mod:`confflow.execution.native`, :mod:`confflow.domain`, and the standard
library.

Supported native keys for :func:`render_irc_blocks`
----------------------------------------------------
``direction`` (``"both"``, the default) and ``max_iter`` (positive ``int``,
rendered as ``MaxIter``) are the only accepted keys.  ``"both"`` is the only
supported direction: any request for a forward-only or reverse-only IRC (for
example ``"forward"`` or ``"reverse"``) raises ``ValueError`` with a
``native_input_error: ... unsupported ...`` message.  Any other key raises the
same fail-closed error and is never passed through to the input file.
``MaxIter`` is emitted into the ``%geom`` block; verified against the
installed binary, it caps IRC iterations per direction exactly like the
``%irc`` spelling.

Real ORCA 6.1 output grammar for :func:`parse_path_endpoints`
-------------------------------------------------------------
- Section banners read ``* FORWARD IRC *`` / ``* BACKWARD IRC *``
  (asterisk framing, case-sensitive); a missing section means that
  direction is absent (the result profile demands exactly one forward
  plus one reverse endpoint and fails closed otherwise), while a
  duplicated section fails closed as ambiguous.
- Iteration rows read ``<int> <E_hartree> <dE> <maxG> <rmsG>``; the last
  row of a section gives the endpoint point ordinal and energy.
- ``MAXIMUM NUMBER OF ITERATIONS REACHED`` inside a section marks its
  endpoint unconverged (still reported with ``converged=False``); the
  result profile decides what that means.
- Endpoint geometries come from ``<log_base>_IRC_F.xyz`` /
  ``<log_base>_IRC_B.xyz`` in the work directory (standard multi-line
  XYZ: count, ``Coordinates from ORCA-job ... E <float>`` comment,
  coordinate rows).  A missing or malformed endpoint file fails closed;
  a comment energy disagreeing with the table energy beyond 1e-3
  Hartree fails closed.
- Atom count and symbols must match the expected ``atoms`` exactly.
  Returned endpoints are always ordered ``(forward, reverse)``.

Trajectory facts for :func:`path_trajectory_facts`
---------------------------------------------------
Best-effort iteration rows with the truncation marker above; unparseable
lines are skipped, never fatal.
"""

from __future__ import annotations

import dataclasses
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ...domain.elements import canonical_element_symbol
from ...execution.native import NativePathEndpoint, ParsedGeometry

__all__ = [
    "IRC_TRUNCATED_MARKER",
    "SUPPORTED_IRC_KEYS",
    "parse_path_endpoints",
    "path_trajectory_facts",
    "render_irc_blocks",
]

#: Marker reporting a truncated IRC direction in real ORCA 6.1 output.
IRC_TRUNCATED_MARKER: str = "MAXIMUM NUMBER OF ITERATIONS REACHED"

#: Exhaustive allowlist of native keys accepted by :func:`render_irc_blocks`.
SUPPORTED_IRC_KEYS: frozenset[str] = frozenset({"direction", "max_iter"})

#: Only bidirectional IRC is supported; direction requests are normalized here.
_SUPPORTED_DIRECTIONS: frozenset[str] = frozenset({"both"})

#: Real ORCA 6.1 IRC iteration row: ``<iter> <E_h> <dE> <maxG> <rmsG>``.
_ITERATION_ROW_RE = re.compile(r"^\s*(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")

#: Real asterisk-framed direction section banners.
_SECTION_BANNER_RE = re.compile(r"^\*+\s+(FORWARD|BACKWARD) IRC\s+\*+\s*$")

#: Real XYZ energy comment: ``Coordinates from ORCA-job <base> E <float>``.
_XYZ_ENERGY_RE = re.compile(r"^Coordinates from ORCA-job\s+\S+\s+E\s+(\S+)\s*$")


def _input_error(message: str) -> ValueError:
    """Build a native-input ``ValueError`` carrying the stable error code.

    Parameters
    ----------
    message : str
        Human-readable explanation of the invalid input.

    Returns
    -------
    ValueError
        Exception whose message starts with ``"native_input_error"``.
    """
    return ValueError(f"native_input_error: {message}")


def render_irc_blocks(native: Mapping[str, Any]) -> str:
    """Render the ``%geom`` IRC modifier block from allowlisted native keys.

    Parameters
    ----------
    native : Mapping[str, Any]
        Native option mapping holding only ``direction`` (``"both"``) and/or
        ``max_iter`` (positive ``int``).  Any other key is rejected.

    Returns
    -------
    str
        Rendered ``%geom`` block ending with a newline.

    Raises
    ------
    ValueError
        Raised with a ``native_input_error`` message when ``native`` is not
        a mapping, carries unknown keys, requests an unsupported direction,
        or holds a non-positive-integer ``max_iter``.
    """
    if not isinstance(native, Mapping):
        raise _input_error(f"ORCA IRC 'native' must be a mapping, got {type(native).__name__}")
    unknown = sorted(set(native) - set(SUPPORTED_IRC_KEYS))
    if unknown:
        raise _input_error(f"ORCA IRC unsupported native keys: {', '.join(unknown)}")

    direction = native.get("direction", "both")
    if not isinstance(direction, str) or direction.strip().lower() not in _SUPPORTED_DIRECTIONS:
        raise _input_error(
            f"ORCA IRC unsupported direction {direction!r}; only 'both' is supported"
        )

    max_iter = native.get("max_iter")
    max_iter_line = ""
    if max_iter is not None:
        if isinstance(max_iter, bool) or not isinstance(max_iter, int) or max_iter < 1:
            raise _input_error(f"ORCA IRC 'max_iter' must be a positive integer, got {max_iter!r}")
        max_iter_line = f"  MaxIter {max_iter}\n"
    return f"%geom\n{max_iter_line}end\n"


def _parse_coord_line(line: str) -> tuple[str, float, float, float]:
    """Parse one ``<symbol> <x> <y> <z>`` line with element validation.

    Parameters
    ----------
    line : str
        Candidate coordinate line.

    Returns
    -------
    tuple[str, float, float, float]
        Canonical ``(symbol, x, y, z)`` values.

    Raises
    ------
    ValueError
        Raised when the line is not a valid coordinate line.
    """
    parts = line.split()
    if len(parts) != 4:
        raise ValueError(f"expected '<symbol> <x> <y> <z>', got {line!r}")
    try:
        symbol = canonical_element_symbol(parts[0])
        point = (float(parts[1]), float(parts[2]), float(parts[3]))
    except (ValueError, ArithmeticError) as exc:
        raise ValueError(f"invalid coordinate line {line!r}: {exc}") from exc
    if not all(math.isfinite(value) for value in point):
        raise ValueError(f"non-finite coordinates in line {line!r}")
    return symbol, point[0], point[1], point[2]


@dataclasses.dataclass
class _IrcSection:
    """One parsed real IRC direction section."""

    direction: str
    iterations: list[tuple[int, float]] = dataclasses.field(default_factory=list)
    truncated: bool = False


def _split_irc_sections(text: str) -> dict[str, _IrcSection]:
    """Split log text into forward/backward IRC sections.

    A section runs from its asterisk-framed banner to the next banner
    (or end of file).  Missing sections are absent (never inferred);
    duplicated sections fail closed as ambiguous.
    """
    sections: dict[str, _IrcSection] = {}
    current: _IrcSection | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if "IRC PATH SUMMARY" in line:
            # The summary table reprints both directions' energies outside
            # any section; its rows belong to neither direction.
            current = None
            continue
        banner = _SECTION_BANNER_RE.match(line)
        if banner is not None:
            direction = "forward" if banner.group(1) == "FORWARD" else "backward"
            if direction in sections:
                raise _input_error(
                    f"ORCA IRC {direction} section appears more than once; "
                    "endpoints are ambiguous"
                )
            current = _IrcSection(direction=direction)
            sections[direction] = current
            continue
        if current is None:
            continue
        if IRC_TRUNCATED_MARKER in line:
            current.truncated = True
            continue
        row = _ITERATION_ROW_RE.match(line)
        if row is None:
            continue
        try:
            ordinal = int(row.group(1))
            energy = float(row.group(2))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(energy):
            continue
        current.iterations.append((ordinal, energy))
    return sections


def _parse_xyz_file(path: str, *, expected: Sequence[str]) -> tuple[ParsedGeometry, float | None]:
    """Parse one real ORCA XYZ endpoint file.

    Standard XYZ: atom count, comment line (carrying
    ``Coordinates from ORCA-job <base> E <float>`` when the program
    wrote energies), then coordinate rows.  Atom count and symbols must
    match ``expected`` exactly; a missing or malformed file fails
    closed.  Returns the geometry plus the comment energy (``None``
    when the comment carries no parseable value).
    """
    try:
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
    except OSError as exc:
        raise _input_error(f"ORCA IRC endpoint file is not readable: {path!r}: {exc}") from exc
    lines = content.splitlines()
    if len(lines) < 2:
        raise _input_error(f"ORCA IRC endpoint file {path!r} has no XYZ header")
    try:
        count = int(lines[0].strip())
    except (TypeError, ValueError) as exc:
        raise _input_error(f"ORCA IRC endpoint file {path!r} carries no atom count: {exc}") from exc
    comment_energy: float | None = None
    comment_match = _XYZ_ENERGY_RE.match(lines[1].strip())
    if comment_match is not None:
        try:
            candidate = float(comment_match.group(1))
        except (TypeError, ValueError):
            candidate = math.nan
        if math.isfinite(candidate):
            comment_energy = candidate
    rows = lines[2 : 2 + count]
    if len(rows) != count:
        raise _input_error(
            f"ORCA IRC endpoint file {path!r} holds {len(rows)} rows for {count} atoms"
        )
    atoms: list[str] = []
    coordinates: list[tuple[float, float, float]] = []
    for line in rows:
        atoms.append(_parse_coord_line(line)[0])
        coordinates.append(_parse_coord_line(line)[1:])
    if tuple(atoms) != tuple(expected):
        raise _input_error(
            f"ORCA IRC endpoint file {path!r} symbols {atoms} disagree "
            f"with expected {list(expected)}"
        )
    return ParsedGeometry(atoms=tuple(atoms), coordinates=tuple(coordinates)), comment_energy


def parse_path_endpoints(
    text: str,
    *,
    atoms: Sequence[str],
    work_dir: str,
    log_base: str,
) -> tuple[NativePathEndpoint, ...]:
    """Parse forward/reverse IRC endpoints from real ORCA 6.1 output.

    Parameters
    ----------
    text : str
        Full ORCA log file content.
    atoms : Sequence[str]
        Expected element symbols in atom order; every endpoint geometry
        must match both the count and the symbols.
    work_dir : str
        Directory holding the ``<log_base>_IRC_F.xyz`` /
        ``<log_base>_IRC_B.xyz`` endpoint files.
    log_base : str
        Log file stem naming the endpoint files.

    Returns
    -------
    tuple[NativePathEndpoint, ...]
        ``(forward, reverse)`` endpoints in canonical order.  ORCA's
        ``BACKWARD`` direction maps to ``reverse`` (recorded in native
        metadata); a missing section means that direction is absent and
        the result profile fails closed downstream.

    Raises
    ------
    ValueError
        Raised when a section is duplicated, an endpoint file is
        missing or malformed, table and file energies disagree, or a
        geometry disagrees with ``atoms``.
    """
    import os as _os

    expected = tuple(atoms)
    sections = _split_irc_sections(text)
    endpoints: list[NativePathEndpoint] = []
    for native_direction, canonical, suffix in (
        ("forward", "forward", "_IRC_F.xyz"),
        ("backward", "reverse", "_IRC_B.xyz"),
    ):
        section = sections.get(native_direction)
        if section is None:
            continue
        if not section.iterations:
            raise _input_error(f"ORCA IRC {native_direction} section carries no iteration rows")
        ordinal, table_energy = section.iterations[-1]
        candidate = _os.path.join(work_dir, f"{log_base}{suffix}")
        geometry, file_energy = _parse_xyz_file(candidate, expected=expected)
        if file_energy is not None and abs(file_energy - table_energy) > 1e-3:
            raise _input_error(
                f"ORCA IRC {native_direction} endpoint file energy "
                f"{file_energy!r} disagrees with table energy {table_energy!r}"
            )
        endpoints.append(
            NativePathEndpoint(
                direction=canonical,
                geometry=geometry,
                point_ordinal=ordinal,
                energy_hartree=table_energy,
                converged=not section.truncated,
                metadata={"native_direction": native_direction.upper()},
            )
        )
    return tuple(endpoints)


def path_trajectory_facts(text: str) -> dict[str, Any]:
    """Summarize IRC trajectory point facts on a best-effort basis.

    Scans real ORCA 6.1 ``FORWARD IRC`` / ``BACKWARD IRC`` sections for
    iteration rows (``<iter> <E_h> <dE> <maxG> <rmsG>``); unparseable
    lines are skipped, never fatal.  ``BACKWARD`` rows count as reverse
    points, mirroring :func:`parse_path_endpoints`.

    Parameters
    ----------
    text : str
        Full log file content.

    Returns
    -------
    dict[str, Any]
        ``n_points``, ``n_forward_points``, ``n_reverse_points``,
        ``energies_hartree`` (encounter-order tuple), and ``truncated``
        (whether ``MAXIMUM NUMBER OF ITERATIONS REACHED`` is present).
        Unparseable lines are skipped; this function never raises on
        malformed input.
    """
    energies: list[float] = []
    n_forward = 0
    n_reverse = 0
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if "IRC PATH SUMMARY" in line:
            # Summary rows reprint both directions outside any section.
            current = None
            continue
        banner = _SECTION_BANNER_RE.match(line)
        if banner is not None:
            current = "forward" if banner.group(1) == "FORWARD" else "reverse"
            continue
        if current is None:
            continue
        match = _ITERATION_ROW_RE.match(line)
        if match is None:
            continue
        try:
            _ordinal = int(match.group(1))
            energy = float(match.group(2))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(energy):
            continue
        energies.append(energy)
        if current == "forward":
            n_forward += 1
        else:
            n_reverse += 1
    return {
        "n_points": n_forward + n_reverse,
        "n_forward_points": n_forward,
        "n_reverse_points": n_reverse,
        "energies_hartree": tuple(energies),
        "truncated": IRC_TRUNCATED_MARKER in text,
    }
