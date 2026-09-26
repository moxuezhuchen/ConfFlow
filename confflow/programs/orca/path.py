#!/usr/bin/env python3

"""ORCA reaction-path (IRC) helpers for ConfFlow Workflow V4.

This module renders ``%geom`` IRC modifiers and parses IRC endpoints from a
documented minimal output dialect.  It owns V4-native copies of small parsing
idioms (it never imports the legacy calc runtime, the program adapter, or the
sibling rendering/parsing helpers) and imports only
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

Capability boundary
-------------------
The version-specific IRC activation trigger (the exact keyword or ``%geom``
activation key selecting IRC across ORCA versions) is intentionally not
rendered here: this helper was implemented from documented ORCA semantics
(``%geom`` blocks drive geometry jobs; IRC jobs follow the intrinsic
reaction coordinate in both directions) with zero legacy IRC syntax present
in this repository to port.  Only the generic ``MaxIter`` modifier above is
emitted; the IRC job-type keyword itself travels through the normal keyword
path.  Every IRC tuning key outside the allowlist (step sizes, Hessian
recalculation schedules, convergence overrides, and so on) is rejected as
unsupported rather than guessed.

Minimal output dialect for :func:`parse_path_endpoints`
-------------------------------------------------------
Direction comes from explicit banners only and is never inferred from order::

    CONFFLOW IRC FORWARD ENDPOINT
    ENERGY -76.123456
    CONVERGED true
    POINT 12
    GEOMETRY
    O 0.000000 0.000000 0.000000
    H 0.760000 0.590000 0.000000
    END GEOMETRY

Each banner (``CONFFLOW IRC FORWARD ENDPOINT`` /
``CONFFLOW IRC REVERSE ENDPOINT``) must appear exactly once; a missing or
duplicated banner raises ``ValueError``.  Each section requires exactly one
``ENERGY <float>`` line, exactly one ``CONVERGED true|false`` line, and
exactly one ``GEOMETRY`` ... ``END GEOMETRY`` block whose atom count and
symbols must match the expected ``atoms``.  ``POINT <int>`` is optional and
feeds ``point_ordinal``.  Blank lines are ignored; any other line inside a
section raises ``ValueError``.  Non-blank lines before the first banner must
belong to the trajectory dialect (``IRC POINT ...`` lines or the truncation
marker) or they are rejected.  Returned endpoints are always ordered
``(forward, reverse)`` regardless of banner order in the text.

Trajectory dialect for :func:`path_trajectory_facts`
-----------------------------------------------------
Best-effort point lines (unparseable lines are skipped, never fatal)::

    IRC POINT 3 FORWARD ENERGY -76.100000
    CONFFLOW IRC TRUNCATED

The ``CONFFLOW IRC TRUNCATED`` marker sets the ``truncated`` fact.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ...domain.elements import canonical_element_symbol
from ...execution.native import NativePathEndpoint, ParsedGeometry

__all__ = [
    "IRC_FORWARD_BANNER",
    "IRC_REVERSE_BANNER",
    "IRC_TRUNCATED_MARKER",
    "SUPPORTED_IRC_KEYS",
    "parse_path_endpoints",
    "path_trajectory_facts",
    "render_irc_blocks",
]

#: Banner marking the forward IRC endpoint section in the minimal dialect.
IRC_FORWARD_BANNER: str = "CONFFLOW IRC FORWARD ENDPOINT"

#: Banner marking the reverse IRC endpoint section in the minimal dialect.
IRC_REVERSE_BANNER: str = "CONFFLOW IRC REVERSE ENDPOINT"

#: Marker reporting a truncated IRC trajectory in the minimal dialect.
IRC_TRUNCATED_MARKER: str = "CONFFLOW IRC TRUNCATED"

#: Exhaustive allowlist of native keys accepted by :func:`render_irc_blocks`.
SUPPORTED_IRC_KEYS: frozenset[str] = frozenset({"direction", "max_iter"})

#: Only bidirectional IRC is supported; direction requests are normalized here.
_SUPPORTED_DIRECTIONS: frozenset[str] = frozenset({"both"})

_GEOMETRY_BEGIN: str = "GEOMETRY"
_GEOMETRY_END: str = "END GEOMETRY"

_POINT_LINE_PATTERN = re.compile(r"^IRC POINT\s+(\d+)\s+(FORWARD|REVERSE)\s+ENERGY\s+(\S+)\s*$")


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


def _split_endpoint_sections(text: str) -> dict[str, list[str]]:
    """Split log text into forward/reverse endpoint section line lists.

    Parameters
    ----------
    text : str
        Full log file content in the minimal IRC dialect.

    Returns
    -------
    dict[str, list[str]]
        ``{"forward": [...], "reverse": [...]}`` raw section lines.

    Raises
    ------
    ValueError
        Raised when a banner is missing or duplicated, or when a non-blank
        line outside sections falls outside the trajectory dialect.
    """
    lines = text.splitlines()
    forward_hits = [i for i, line in enumerate(lines) if line.strip() == IRC_FORWARD_BANNER]
    reverse_hits = [i for i, line in enumerate(lines) if line.strip() == IRC_REVERSE_BANNER]
    if len(forward_hits) != 1:
        raise ValueError(
            f"IRC forward banner {IRC_FORWARD_BANNER!r} must appear exactly once, "
            f"found {len(forward_hits)}"
        )
    if len(reverse_hits) != 1:
        raise ValueError(
            f"IRC reverse banner {IRC_REVERSE_BANNER!r} must appear exactly once, "
            f"found {len(reverse_hits)}"
        )
    banner_positions = {forward_hits[0], reverse_hits[0]}
    sections: dict[str, list[str]] = {"forward": [], "reverse": []}
    current: str | None = None
    for index, line in enumerate(lines):
        if index in banner_positions:
            current = "forward" if index == forward_hits[0] else "reverse"
            continue
        if current is None:
            # Lines before the first banner are outside every section: only
            # blanks and trajectory-dialect lines may appear there.
            if not line.strip():
                continue
            stripped = line.strip()
            if _POINT_LINE_PATTERN.match(stripped) or stripped == IRC_TRUNCATED_MARKER:
                continue
            raise ValueError(f"line outside IRC endpoint sections: {line!r}")
        sections[current].append(line)
    return sections


def _parse_endpoint_section(
    direction: str, lines: Sequence[str], *, atoms: Sequence[str]
) -> NativePathEndpoint:
    """Parse one endpoint section into a :class:`NativePathEndpoint` fact.

    Parameters
    ----------
    direction : str
        ``"forward"`` or ``"reverse"`` taken from the section banner.
    lines : Sequence[str]
        Raw section lines between banners.
    atoms : Sequence[str]
        Expected element symbols in atom order.

    Returns
    -------
    NativePathEndpoint
        Parsed endpoint fact.

    Raises
    ------
    ValueError
        Raised when required lines are missing, duplicated, or malformed, or
        when the geometry disagrees with ``atoms``.
    """
    try:
        expected = [canonical_element_symbol(symbol) for symbol in atoms]
    except (ValueError, ArithmeticError) as exc:
        raise ValueError(f"invalid expected atoms: {exc}") from exc
    if not expected:
        raise ValueError("expected atoms must not be empty")

    energy: float | None = None
    energy_hits = 0
    converged: bool | None = None
    converged_hits = 0
    point_ordinal: int | None = None
    point_hits = 0
    geometry: tuple[tuple[str, ...], tuple[tuple[float, ...], ...]] | None = None
    geometry_hits = 0
    in_geometry = False
    geometry_atoms: list[str] = []
    geometry_coords: list[tuple[float, ...]] = []

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line == "****ORCA TERMINATED NORMALLY****":
            # Trailing program termination marker after the last section:
            # the termination fact is read separately, never as section data.
            continue
        if in_geometry:
            if line == _GEOMETRY_END:
                in_geometry = False
                geometry_hits += 1
                geometry = (tuple(geometry_atoms), tuple(geometry_coords))
                geometry_atoms = []
                geometry_coords = []
                continue
            symbol, x, y, z = _parse_coord_line(line)
            geometry_atoms.append(symbol)
            geometry_coords.append((x, y, z))
            continue
        if line == _GEOMETRY_BEGIN:
            if geometry_hits:
                raise ValueError(f"IRC {direction} section has a duplicate GEOMETRY block")
            in_geometry = True
            continue
        if line == _GEOMETRY_END:
            raise ValueError(f"IRC {direction} section has END GEOMETRY without GEOMETRY")
        parts = line.split(None, 1)
        keyword = parts[0].upper()
        rest = parts[1].strip() if len(parts) == 2 else ""
        if keyword == "ENERGY":
            energy_hits += 1
            if energy_hits > 1:
                raise ValueError(f"IRC {direction} section has a duplicate ENERGY line")
            try:
                energy = float(rest)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"IRC {direction} section has invalid energy {rest!r}") from exc
            if not math.isfinite(energy):
                raise ValueError(f"IRC {direction} section has non-finite energy {rest!r}")
            continue
        if keyword == "CONVERGED":
            converged_hits += 1
            if converged_hits > 1:
                raise ValueError(f"IRC {direction} section has a duplicate CONVERGED line")
            lowered = rest.lower()
            if lowered == "true":
                converged = True
            elif lowered == "false":
                converged = False
            else:
                raise ValueError(f"IRC {direction} section has invalid converged flag {rest!r}")
            continue
        if keyword == "POINT":
            point_hits += 1
            if point_hits > 1:
                raise ValueError(f"IRC {direction} section has a duplicate POINT line")
            try:
                point_ordinal = int(rest)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"IRC {direction} section has invalid point ordinal {rest!r}"
                ) from exc
            if isinstance(point_ordinal, bool) or point_ordinal < 0:
                raise ValueError(f"IRC {direction} section has invalid point ordinal {rest!r}")
            continue
        raise ValueError(f"IRC {direction} section has unknown line {raw!r}")

    if in_geometry:
        raise ValueError(f"IRC {direction} section has unterminated GEOMETRY block")
    if energy_hits != 1 or energy is None:
        raise ValueError(f"IRC {direction} section requires exactly one ENERGY line")
    if converged_hits != 1 or converged is None:
        raise ValueError(f"IRC {direction} section requires exactly one CONVERGED line")
    if geometry_hits != 1 or geometry is None:
        raise ValueError(f"IRC {direction} section requires exactly one GEOMETRY block")
    parsed_atoms, parsed_coords = geometry
    if len(parsed_atoms) != len(expected):
        count = len(parsed_atoms)
        raise ValueError(f"IRC {direction} endpoint has {count} atoms, expected {len(expected)}")
    for index, (found, wanted) in enumerate(zip(parsed_atoms, expected)):
        if found != wanted:
            raise ValueError(
                f"IRC {direction} endpoint atom {index} is {found!r}, expected {wanted!r}"
            )
    return NativePathEndpoint(
        direction=direction,
        geometry=ParsedGeometry(
            atoms=tuple(parsed_atoms),
            coordinates=tuple(tuple(point) for point in parsed_coords),
        ),
        point_ordinal=point_ordinal,
        energy_hartree=energy,
        converged=converged,
    )


def parse_path_endpoints(text: str, *, atoms: Sequence[str]) -> tuple[NativePathEndpoint, ...]:
    """Parse forward/reverse IRC endpoints from the minimal dialect.

    Parameters
    ----------
    text : str
        Full log file content in the minimal IRC dialect.
    atoms : Sequence[str]
        Expected element symbols in atom order; every endpoint geometry must
        match both the count and the symbols.

    Returns
    -------
    tuple[NativePathEndpoint, ...]
        ``(forward, reverse)`` endpoints in canonical order regardless of
        banner order in ``text``.

    Raises
    ------
    ValueError
        Raised when a banner is missing or duplicated, or when a section is
        malformed or disagrees with ``atoms``.
    """
    sections = _split_endpoint_sections(text)
    forward = _parse_endpoint_section("forward", sections["forward"], atoms=atoms)
    reverse = _parse_endpoint_section("reverse", sections["reverse"], atoms=atoms)
    return (forward, reverse)


def path_trajectory_facts(text: str) -> dict[str, Any]:
    """Summarize IRC trajectory point facts on a best-effort basis.

    Parameters
    ----------
    text : str
        Full log file content, possibly carrying ``IRC POINT`` lines.

    Returns
    -------
    dict[str, Any]
        ``n_points``, ``n_forward_points``, ``n_reverse_points``,
        ``energies_hartree`` (encounter-order tuple), and ``truncated``
        (whether ``CONFFLOW IRC TRUNCATED`` is present).  Unparseable lines
        are skipped; this function never raises on malformed input.
    """
    energies: list[float] = []
    n_forward = 0
    n_reverse = 0
    for raw in text.splitlines():
        match = _POINT_LINE_PATTERN.match(raw.strip())
        if match is None:
            continue
        try:
            _ordinal = int(match.group(1))
            energy = float(match.group(3))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(energy):
            continue
        energies.append(energy)
        if match.group(2) == "FORWARD":
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
