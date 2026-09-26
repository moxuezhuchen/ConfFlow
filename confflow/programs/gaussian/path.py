#!/usr/bin/env python3

"""Gaussian reaction-path (IRC) route and endpoint helpers for V4-5.

Pure helpers over real Gaussian 16 IRC semantics, verified against vendor
IRC logs (``/opt/g16/tests/amd64/test0313.log`` and siblings, Gaussian 16
Revision C.02).  Direction comes only from explicit native direction
announcements; endpoint geometries come only from ``Input orientation``
tables; endpoint energies come only from ``SCF Done`` lines.  Anything
else is ignored or fails closed — never inferred.

Real Gaussian 16 IRC grammar (evidence: vendor logs above)
----------------------------------------------------------
- ``Point Number  N in FORWARD path direction.`` /
  ``Point Number  N in REVERSE path direction.`` announces the direction
  of the following points.  Points before the first announcement (the
  transition-state origin, ``Point Number: 0``) carry no direction and
  are never endpoints.
- ``Point Number:   N          Path Number:   M`` opens point ``N`` of
  path ``M``.  Only points inside the IRC scope (first announcement
  through ``Reaction path calculation complete.`` or end of file) are
  considered, so Opt/Freq sections of multi-step logs never leak in.
- ``Input orientation:`` tables carry ``Center / Atomic Number / Type /
  X Y Z`` rows in Angstrom; the last table of a point's span wins.
- ``SCF Done:  E(<method>) =  <float>     A.U.`` carries the point
  Hartree energy; the last one of a point's span wins (absent or
  unparsable means ``None``, never zero).
- ``Calculation of FORWARD path complete.`` /
  ``Calculation of REVERSE path complete.`` /
  ``Beginning calculation of the REVERSE path.`` /
  ``Reaction path calculation complete.`` delimit direction sections.
- Endpoints are the maximum point number per ``(direction, path)`` group.
  Each direction present must span exactly one path number, else the log
  is ambiguous and parsing fails closed.  A direction with no points is
  simply absent (the result profile demands exactly one forward plus one
  reverse endpoint and fails closed on anything else).
- Convergence is the literal ``Reaction path calculation complete.``
  marker: present means converged endpoints, absent means the path
  stopped early (endpoints still parse with ``converged=False``).

Fail-closed boundaries: a second path number inside one direction, an
endpoint geometry whose atom count or symbols disagree with the expected
atoms, an unknown atomic number, and a point header outside any direction
(other than the unattributed origin) all raise ``ValueError``.  Return
order is forward-then-reverse; the profile re-sorts by direction for
identity.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from ...domain.elements import canonical_element_symbol
from ...domain.errors import ElementSymbolError
from ...execution.native import NativePathEndpoint, ParsedGeometry

__all__ = [
    "irc_trajectory_facts",
    "parse_irc_endpoints",
    "parse_irc_route",
]

#: Real Gaussian 16 IRC direction announcement, e.g.
#: ``Point Number  1 in FORWARD path direction.``
_DIRECTION_ANNOUNCE_RE: Final = re.compile(
    r"Point Number\s+(\d+)\s+in\s+(FORWARD|REVERSE)\s+path direction\."
)

#: Real point header, e.g. ``Point Number:   3          Path Number:   1``.
_POINT_HEADER_RE: Final = re.compile(r"Point Number:\s*(\d+)\s+Path Number:\s*(\d+)")

#: Real SCF energy line, e.g. ``SCF Done:  E(RHF) =  -91.57     A.U.``.
_SCF_DONE_RE: Final = re.compile(r"SCF Done:\s+E\(\S+\)\s*=\s*(\S+)\s+A\.U\.")

#: Real overall path-completion marker (convergence fact).
_PATH_COMPLETE_MARKER: Final[str] = "Reaction path calculation complete."

#: Real orientation-table header.
_ORIENTATION_HEADER: Final[str] = "Input orientation:"

#: Direction votes recognized inside ``IRC(...)``, case-insensitively.
_DIRECTION_TOKENS: Final[dict[str, str]] = {
    "RCFC": "both",
    "FORWARD": "forward",
    "REVERSE": "reverse",
}

#: Documented non-direction IRC options passed through opaquely.
_OPAQUE_IRC_OPTIONS: Final[frozenset[str]] = frozenset(
    {"MAXPOINTS", "STEPSIZE", "RECALCFC", "CALCFC"}
)

_IRC_ITEM_PATTERN: Final = re.compile(r"(?<![A-Za-z0-9])IRC(?![A-Za-z0-9])", re.IGNORECASE)


def _finite_float(token: str) -> float | None:
    """Return a finite float for *token*, or ``None`` when unparseable.

    Parameters
    ----------
    token : str
        Candidate numeric token.

    Returns
    -------
    float | None
        Finite float value, or ``None`` for missing, malformed, or
        non-finite input.
    """
    try:
        value = float(token)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _orientation_row(line: str) -> tuple[str, float, float, float] | None:
    """Parse one orientation-table coordinate row, or return ``None``.

    Real Gaussian 16 prints two flavors: the ``Input orientation`` form
    ``<center> <Z> <type> <x> <y> <z>`` and the compact ``Center / Atomic
    / Coordinates`` form ``<center> <Z> <x> <y> <z>``.  Atomic numbers map
    through the element table (out-of-range numbers fail closed); the
    center index and atom type never participate in identity.  Fortran
    ``D`` exponents are accepted alongside ``E``.
    """
    from ...domain.elements import ELEMENT_SYMBOLS

    parts = line.split()
    if len(parts) == 6:
        number_token, coord_tokens = parts[1], parts[3:6]
    elif len(parts) == 5:
        number_token, coord_tokens = parts[1], parts[2:5]
    else:
        return None
    try:
        atomic_number = int(number_token)
    except (TypeError, ValueError):
        return None
    if atomic_number < 1 or atomic_number >= len(ELEMENT_SYMBOLS):
        return None
    symbol = ELEMENT_SYMBOLS[atomic_number]
    if not symbol:
        return None
    try:
        point = tuple(float(token.replace("D", "E").replace("d", "E")) for token in coord_tokens)
    except (TypeError, ValueError):
        return None
    if len(point) != 3 or not all(math.isfinite(value) for value in point):
        return None
    return (symbol, point[0], point[1], point[2])


def _announced_direction(line: str) -> str | None:
    """Return the direction announced by a real IRC log line, if any.

    Matches ``Point Number  N in FORWARD path direction.`` (and the
    REVERSE form) exactly; a line announcing both directions is
    impossible by construction of the alternation and needs no guard.
    """
    match = _DIRECTION_ANNOUNCE_RE.search(line)
    if match is None:
        return None
    return "forward" if match.group(2) == "FORWARD" else "reverse"


@dataclass
class _PathPoint:
    """Mutable accumulator for one real IRC point."""

    direction: str
    number: int
    path: int
    rows: list[tuple[str, float, float, float]] | None = None
    energy: float | None = None


def parse_irc_route(keyword: str) -> dict[str, Any]:
    """Parse the IRC direction out of a Gaussian route line.

    Only the first ``IRC`` item in *keyword* is read; the rest of the
    route line is ignored. Option tokens are matched case-insensitively:
    ``RCFC`` votes ``"both"``, ``Forward`` votes ``"forward"``,
    ``Reverse`` votes ``"reverse"``. Key=value tokens (``MaxPoints=20``)
    and the documented non-direction options (``MaxPoints``, ``StepSize``,
    ``RecalcFC``, ``CalcFC``) pass through opaquely in ``raw_options``;
    any other bare token is rejected because it may be a misspelled
    direction. Conflicting direction votes are rejected.

    A bare ``IRC`` item (no options, empty ``IRC()``, or no direction
    token) reports mode ``"both"``. Rationale, stated explicitly rather
    than guessed per calculation: ``RCFC`` is the documented
    both-directions form and a bare IRC job started from a transition
    state follows the path forward and reverse by default.

    Parameters
    ----------
    keyword : str
        Full Gaussian route line, for example
        ``"B3LYP/6-31G* IRC(RCFC,MaxPoints=20)"``.

    Returns
    -------
    dict[str, Any]
        ``{"mode": "both" | "forward" | "reverse", "raw_options": (...)}``
        where ``raw_options`` preserves the encounter order and original
        case of the option tokens with internal whitespace folded away.

    Raises
    ------
    ValueError
        Raised as ``native_input_error`` when *keyword* is not a
        non-empty string, carries no IRC item, has a malformed option
        group, carries an unknown bare option token, or votes for
        conflicting directions.
    """
    if not isinstance(keyword, str) or not keyword.strip():
        raise ValueError("native_input_error: Gaussian IRC route must be a non-empty string")
    match = _IRC_ITEM_PATTERN.search(keyword)
    if match is None:
        raise ValueError(
            "native_input_error: Gaussian route carries no IRC item; " f"got {keyword.strip()!r}"
        )
    text = keyword[match.end() :].lstrip()
    had_equals = False
    if text.startswith("="):
        had_equals = True
        text = text[1:].lstrip()
    if text.startswith("("):
        closing = text.find(")")
        if closing < 0:
            raise ValueError("native_input_error: Gaussian IRC option group is missing ')'")
        tokens = [token for token in text[1:closing].split(",")]
    elif had_equals:
        token = text.split(",")[0].strip() if text else ""
        if not token or len(token.split()) != 1 or "," in text:
            raise ValueError("native_input_error: Gaussian IRC= takes a single option token")
        tokens = [token]
    else:
        return {"mode": "both", "raw_options": ()}
    folded = ["".join(token.split()) for token in tokens]
    folded = [token for token in folded if token]
    if not folded:
        return {"mode": "both", "raw_options": ()}
    votes: set[str] = set()
    raw_options: list[str] = []
    for token in folded:
        upper = token.upper()
        raw_options.append(token)
        if upper in _DIRECTION_TOKENS:
            votes.add(_DIRECTION_TOKENS[upper])
        elif "=" in token or upper in _OPAQUE_IRC_OPTIONS:
            continue
        else:
            raise ValueError(
                "native_input_error: Gaussian IRC got unknown option "
                f"{token!r}; direction must be RCFC, Forward, or Reverse"
            )
    if len(votes) > 1:
        raise ValueError(
            "native_input_error: Gaussian IRC votes for conflicting "
            f"directions {sorted(votes)}; declare exactly one direction"
        )
    mode = next(iter(votes)) if votes else "both"
    return {"mode": mode, "raw_options": tuple(raw_options)}


def _require_expected_atoms(atoms: Sequence[str]) -> tuple[str, ...]:
    """Canonicalize the expected atom symbols.

    Parameters
    ----------
    atoms : Sequence[str]
        Expected element symbols in atom order.

    Returns
    -------
    tuple[str, ...]
        Canonical symbols.

    Raises
    ------
    ValueError
        Raised as ``native_input_error`` when *atoms* is empty, not a
        symbol sequence, or carries an unknown symbol.
    """
    if isinstance(atoms, (str, bytes)) or not isinstance(atoms, Sequence):
        raise ValueError(
            "native_input_error: Gaussian IRC expected atoms must be "
            "a sequence of element symbols"
        )
    expected: list[str] = []
    for position, symbol in enumerate(atoms, start=1):
        try:
            expected.append(canonical_element_symbol(symbol))
        except (ElementSymbolError, TypeError, ValueError) as exc:
            raise ValueError(
                "native_input_error: Gaussian IRC expected atoms "
                f"position {position} is not a known element symbol: {symbol!r}"
            ) from exc
    if not expected:
        raise ValueError("native_input_error: Gaussian IRC expected atoms must not be empty")
    return tuple(expected)


def parse_irc_endpoints(log_text: str, *, atoms: Sequence[str]) -> tuple[NativePathEndpoint, ...]:
    """Parse reaction-path endpoints from real Gaussian 16 IRC output.

    Direction comes from explicit direction announcements only, never
    from section order.  Points before the first announcement (the
    transition-state origin) carry no direction and are never endpoints.
    Parsing is scoped to the IRC section (first announcement through
    ``Reaction path calculation complete.`` or end of file) so Opt/Freq
    sections of multi-step logs never leak in.

    Parameters
    ----------
    log_text : str
        Full Gaussian log text containing an IRC section.
    atoms : Sequence[str]
        Expected element symbols in atom order. Every endpoint geometry
        must carry exactly these symbols in this order.

    Returns
    -------
    tuple[NativePathEndpoint, ...]
        Parsed endpoints in forward-then-reverse order (the profile
        re-sorts by direction for identity; see
        :mod:`confflow.execution.output_identity`).

    Raises
    ------
    TypeError
        Raised when *log_text* is not a string.
    ValueError
        Raised as ``native_input_error`` when one direction spans more
        than one path number, an endpoint geometry is missing, atom
        counts or symbols disagree, or an atomic number is unknown.  An
        absent energy is ``None``, not an error.  A direction with no
        points is simply absent (the result profile demands exactly one
        forward plus one reverse endpoint and fails closed otherwise).
    """
    if not isinstance(log_text, str):
        raise TypeError("log_text must be a string")
    expected = _require_expected_atoms(atoms)
    lines = log_text.splitlines()
    # Scope to the IRC section: first direction announcement through the
    # path-completion marker (or end of file when the path stopped early).
    start = next(
        (index for index, line in enumerate(lines) if _DIRECTION_ANNOUNCE_RE.search(line)),
        None,
    )
    if start is None:
        return ()
    end = next(
        (
            index
            for index, line in enumerate(lines)
            if _PATH_COMPLETE_MARKER in line and index >= start
        ),
        len(lines),
    )
    converged = end < len(lines)
    scoped = lines[start:end]
    points: dict[tuple[str, int, int], _PathPoint] = {}
    direction: str | None = None
    announced: dict[str, set[int]] = {"forward": set(), "reverse": set()}
    current: _PathPoint | None = None
    index = 0
    while index < len(scoped):
        stripped = scoped[index].strip()
        announced_now = _announced_direction(stripped)
        if announced_now is not None:
            # Announcements select the direction for subsequent point
            # headers; they never close the previous point's span (that
            # point's geometry and energy print after the announcement
            # of the next step).
            direction = announced_now
            match = _DIRECTION_ANNOUNCE_RE.search(stripped)
            if match is not None:
                announced[direction].add(int(match.group(1)))
            index += 1
            continue
        header = _POINT_HEADER_RE.search(stripped)
        if header is not None:
            if direction is None:
                # Unattributed origin point (the transition state): not an
                # endpoint and never attributed by guessing.
                current = None
                index += 1
                continue
            number, path = int(header.group(1)), int(header.group(2))
            if number not in announced[direction]:
                raise ValueError(
                    "native_input_error: Gaussian IRC point "
                    f"{number} in {direction} direction was never announced; "
                    "points without announcements are not endpoints"
                )
            key = (direction, path, number)
            point = points.get(key)
            if point is None:
                point = _PathPoint(direction=direction, number=number, path=path)
                points[key] = point
            current = point
            index += 1
            continue
        if stripped == _ORIENTATION_HEADER:
            if current is None or current.direction != direction:
                # Orientation tables of unattributed points (the
                # transition-state origin), of Opt/Freq spillover, or of a
                # direction whose point header has not been seen yet belong
                # to no open point and are never attributed by guessing.
                index += 1
                continue
            # Collect the coordinate rows following the LAST orientation
            # header of this table: header chrome (column titles, dash
            # rules, distance-matrix tails) never matches the strict row
            # pattern, so rows are gathered until the first other content
            # line and the last run wins.  A header with no following rows
            # leaves any previously collected table untouched.
            rows: list[tuple[str, float, float, float]] = []
            index += 1
            while index < len(scoped):
                candidate = scoped[index].strip()
                row = _orientation_row(candidate)
                if row is not None:
                    rows.append(row)
                    index += 1
                    continue
                if (
                    not candidate
                    or set(candidate) <= {"-", " "}
                    or "Center" in candidate
                    or "Atomic" in candidate
                    or "Number" in candidate
                    or "Type" in candidate
                    or "Distance matrix" in candidate
                    or "orientation:" in candidate
                ):
                    index += 1
                    continue
                break
            if rows:
                current.rows = rows
            continue
        energy = _SCF_DONE_RE.search(stripped)
        if energy is not None:
            if current is not None and current.direction == direction:
                current.energy = _finite_float(energy.group(1))
            index += 1
            continue
        index += 1
    grouped: dict[tuple[str, int], list[_PathPoint]] = {}
    for (point_direction, path, _number), point in points.items():
        grouped.setdefault((point_direction, path), []).append(point)
    by_direction: dict[str, list[_PathPoint]] = {}
    for (point_direction, path), members in grouped.items():
        by_direction.setdefault(point_direction, []).append(path)
    for point_direction, paths in by_direction.items():
        if len(set(paths)) > 1:
            raise ValueError(
                "native_input_error: Gaussian IRC direction "
                f"{point_direction!r} spans path numbers {sorted(set(paths))}; "
                "endpoints are ambiguous"
            )
    endpoints: list[NativePathEndpoint] = []
    for point_direction in ("forward", "reverse"):
        candidates = [
            point for (group_direction, _path), members in grouped.items()
            for point in members
            if group_direction == point_direction
        ]
        if not candidates:
            continue
        # The endpoint is the highest-numbered point carrying an
        # orientation geometry.  A trailing announced-but-uncomputed
        # point (reaction-path step limit reached before its gradient
        # evaluation) carries no geometry and can never bind a subject;
        # skipping it is a geometry requirement, not order guessing.
        with_geometry = [point for point in candidates if point.rows is not None]
        if not with_geometry:
            raise ValueError(
                "native_input_error: Gaussian IRC "
                f"{point_direction} direction has no point with an "
                "orientation geometry"
            )
        endpoint_point = max(with_geometry, key=lambda point: point.number)
        if len(endpoint_point.rows) != len(expected):
            raise ValueError(
                "native_input_error: Gaussian IRC "
                f"{point_direction} geometry has {len(endpoint_point.rows)} atoms "
                f"but {len(expected)} were expected"
            )
        symbols = tuple(row[0] for row in endpoint_point.rows)
        if symbols != expected:
            raise ValueError(
                "native_input_error: Gaussian IRC "
                f"{point_direction} geometry symbols {list(symbols)} "
                f"disagree with expected {list(expected)}"
            )
        coordinates = tuple((row[1], row[2], row[3]) for row in endpoint_point.rows)
        endpoints.append(
            NativePathEndpoint(
                direction=point_direction,
                geometry=ParsedGeometry(atoms=symbols, coordinates=coordinates),
                point_ordinal=endpoint_point.number,
                energy_hartree=endpoint_point.energy,
                converged=converged,
            )
        )
    return tuple(endpoints)

def irc_trajectory_facts(log_text: str) -> dict[str, Any]:
    """Summarize IRC trajectory progress without ever raising on content.

    A lenient scan over real Gaussian 16 IRC output: direction
    announcements and point headers are counted per direction, finite
    ``SCF Done`` energies are collected in encounter order, and the
    path-completion marker sets convergence.  Anything unparseable is
    skipped (never inferred); ``"truncated": True`` marks a log that
    ends without the completion marker.

    Parameters
    ----------
    log_text : str
        Full Gaussian log text.

    Returns
    -------
    dict[str, Any]
        ``{"forward_points": int, "reverse_points": int,
        "energies_hartree": [...], "units": "angstrom",
        "truncated": bool, "directions": [...],
        "path_complete": bool}`` with ``directions`` in first-appearance
        order.

    Raises
    ------
    TypeError
        Raised when *log_text* is not a string.
    """
    if not isinstance(log_text, str):
        raise TypeError("log_text must be a string")
    points = {"forward": 0, "reverse": 0}
    energies: list[float] = []
    directions: list[str] = []
    announced: str | None = None
    for line in log_text.splitlines():
        stripped = line.strip()
        direction = _announced_direction(stripped)
        if direction is not None:
            announced = direction
            if direction not in directions:
                directions.append(direction)
            continue
        header = _POINT_HEADER_RE.search(stripped)
        if header is not None:
            if announced is not None:
                points[announced] += 1
            continue
        energy = _SCF_DONE_RE.search(stripped)
        if energy is not None:
            value = _finite_float(energy.group(1))
            if value is not None:
                energies.append(value)
            continue
    path_complete = _PATH_COMPLETE_MARKER in log_text
    return {
        "forward_points": points["forward"],
        "reverse_points": points["reverse"],
        "energies_hartree": energies,
        "units": "angstrom",
        "truncated": not path_complete,
        "directions": directions,
        "path_complete": path_complete,
    }
