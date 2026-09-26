#!/usr/bin/env python3

"""Gaussian reaction-path (IRC) route and endpoint helpers for V4-5.

Pure helpers over documented Gaussian IRC semantics. This module never
touches real Gaussian output: the endpoint parser reads only the minimal
log dialect defined below, and every fixture exercising it is a
handcrafted dialect string (see ``tests/v4/test_v45_gaussian_path.py``).

Minimal IRC log dialect (owned by this module)
----------------------------------------------
- ``IRC FORWARD PATH`` / ``IRC REVERSE PATH`` banner lines select the
  section direction. Matching is a case-sensitive substring test applied
  to a single line; direction never comes from section order, and a line
  containing both banners is rejected as ambiguous.
- ``Endpoint geometry (Angstrom):`` opens one coordinate block. The
  following consecutive ``<symbol> <x> <y> <z>`` lines are the endpoint
  geometry in Angstrom. The block ends at the first blank or
  non-coordinate line. Non-marker lines elsewhere are ignored.
- ``Endpoint SCF energy: <float>`` records the endpoint Hartree energy
  (last occurrence in the section wins; an unparsable or absent value
  means ``None``, never zero).
- ``Endpoint converged: YES`` / ``Endpoint converged: NO`` records
  convergence (last wins; defaults to ``True``; anything else is
  rejected). An unconverged endpoint is still reported with
  ``converged=False``; the result profile decides what that means.
- ``Endpoint point: <int>`` optionally records the native point ordinal
  (last wins; absent means ``None``).

Fail-closed boundaries: a dialect marker outside a bannered section, a
section without exactly one geometry block, a duplicate direction, a
geometry whose atom count or symbols disagree with the expected atoms,
and an ambiguous banner all raise ``ValueError``. Return order is
encounter order; the profile re-sorts by direction for identity.
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
    "CONVERGED_PREFIX",
    "ENERGY_PREFIX",
    "FORWARD_BANNER",
    "GEOMETRY_HEADER",
    "POINT_PREFIX",
    "REVERSE_BANNER",
    "irc_trajectory_facts",
    "parse_irc_endpoints",
    "parse_irc_route",
]

#: Banner marking a forward-direction endpoint section (substring match).
FORWARD_BANNER: Final[str] = "IRC FORWARD PATH"

#: Banner marking a reverse-direction endpoint section (substring match).
REVERSE_BANNER: Final[str] = "IRC REVERSE PATH"

#: Header opening an endpoint coordinate block.
GEOMETRY_HEADER: Final[str] = "Endpoint geometry (Angstrom):"

#: Prefix of the endpoint Hartree-energy line.
ENERGY_PREFIX: Final[str] = "Endpoint SCF energy:"

#: Prefix of the endpoint convergence line.
CONVERGED_PREFIX: Final[str] = "Endpoint converged:"

#: Prefix of the optional native point-ordinal line.
POINT_PREFIX: Final[str] = "Endpoint point:"

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


def _parse_atom_row(line: str) -> tuple[str, float, float, float] | None:
    """Parse one dialect coordinate row, or return ``None``.

    Parameters
    ----------
    line : str
        Candidate ``<symbol> <x> <y> <z>`` line.

    Returns
    -------
    tuple | None
        ``(canonical_symbol, x, y, z)`` for a well-formed row with a
        known element symbol and finite coordinates, else ``None``.
    """
    parts = line.split()
    if len(parts) != 4:
        return None
    try:
        symbol = canonical_element_symbol(parts[0])
    except (ElementSymbolError, TypeError, ValueError):
        return None
    x = _finite_float(parts[1])
    y = _finite_float(parts[2])
    z = _finite_float(parts[3])
    if x is None or y is None or z is None:
        return None
    return (symbol, x, y, z)


def _banner_direction(line: str) -> str | None:
    """Return the section direction selected by a banner line.

    Parameters
    ----------
    line : str
        Single stripped log line.

    Returns
    -------
    str | None
        ``"forward"``, ``"reverse"``, or ``None`` when the line carries
        no banner.

    Raises
    ------
    ValueError
        Raised when the line contains both banners.
    """
    forward = FORWARD_BANNER in line
    reverse = REVERSE_BANNER in line
    if forward and reverse:
        raise ValueError(
            "native_input_error: Gaussian IRC log line carries both "
            "direction banners; direction must be unambiguous"
        )
    if forward:
        return "forward"
    if reverse:
        return "reverse"
    return None


@dataclass
class _EndpointSection:
    """Mutable accumulator for one bannered endpoint section."""

    direction: str
    rows: list[tuple[str, float, float, float]] | None = None
    energy: float | None = None
    converged: bool = True
    point: int | None = None


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
    """Parse reaction-path endpoints from the owned log dialect.

    Direction comes from section banners only, never from section order.
    Sections before the first banner are impossible by construction: any
    dialect marker outside a bannered section raises instead of being
    silently attributed.

    Parameters
    ----------
    log_text : str
        Log content in the module dialect (handcrafted fixtures only;
        never real Gaussian output).
    atoms : Sequence[str]
        Expected element symbols in atom order. Every endpoint geometry
        must carry exactly these symbols in this order.

    Returns
    -------
    tuple[NativePathEndpoint, ...]
        Parsed endpoints in encounter order (the profile re-sorts by
        direction for identity; see
        :mod:`confflow.execution.output_identity`).

    Raises
    ------
    TypeError
        Raised when *log_text* is not a string.
    ValueError
        Raised as ``native_input_error`` when a marker appears outside a
        bannered section, a banner is ambiguous, a direction repeats, a
        section lacks exactly one geometry block, a geometry block is
        empty, atom counts or symbols disagree, or a convergence or point
        flag is malformed. An unparsable energy is ``None``, not an
        error.
    """
    if not isinstance(log_text, str):
        raise TypeError("log_text must be a string")
    expected = _require_expected_atoms(atoms)
    lines = log_text.splitlines()
    sections: list[_EndpointSection] = []
    seen: set[str] = set()
    current: _EndpointSection | None = None
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        direction = _banner_direction(stripped)
        if direction is not None:
            if direction in seen:
                raise ValueError(
                    "native_input_error: Gaussian IRC log repeats the "
                    f"{direction} direction section"
                )
            seen.add(direction)
            current = _EndpointSection(direction=direction)
            sections.append(current)
            index += 1
            continue
        if stripped == GEOMETRY_HEADER:
            if current is None:
                raise ValueError(
                    "native_input_error: Gaussian IRC geometry block "
                    "appears before any direction banner"
                )
            if current.rows is not None:
                raise ValueError(
                    "native_input_error: Gaussian IRC "
                    f"{current.direction} section carries more than one "
                    "geometry block"
                )
            rows: list[tuple[str, float, float, float]] = []
            index += 1
            while index < len(lines):
                row = _parse_atom_row(lines[index])
                if row is None:
                    break
                rows.append(row)
                index += 1
            if not rows:
                raise ValueError(
                    "native_input_error: Gaussian IRC "
                    f"{current.direction} geometry block has no coordinates"
                )
            current.rows = rows
            continue
        if stripped.startswith(ENERGY_PREFIX):
            if current is None:
                raise ValueError(
                    "native_input_error: Gaussian IRC energy line "
                    "appears before any direction banner"
                )
            current.energy = _finite_float(stripped[len(ENERGY_PREFIX) :].strip())
            index += 1
            continue
        if stripped.startswith(CONVERGED_PREFIX):
            if current is None:
                raise ValueError(
                    "native_input_error: Gaussian IRC convergence line "
                    "appears before any direction banner"
                )
            flag = stripped[len(CONVERGED_PREFIX) :].strip().upper()
            if flag == "YES":
                current.converged = True
            elif flag == "NO":
                current.converged = False
            else:
                raise ValueError(
                    "native_input_error: Gaussian IRC convergence flag "
                    f"must be YES or NO, got {flag!r}"
                )
            index += 1
            continue
        if stripped.startswith(POINT_PREFIX):
            if current is None:
                raise ValueError(
                    "native_input_error: Gaussian IRC point line "
                    "appears before any direction banner"
                )
            token = stripped[len(POINT_PREFIX) :].strip()
            try:
                ordinal = int(token)
            except (TypeError, ValueError):
                raise ValueError(
                    "native_input_error: Gaussian IRC point ordinal "
                    f"must be an integer >= 0, got {token!r}"
                ) from None
            if ordinal < 0:
                raise ValueError(
                    "native_input_error: Gaussian IRC point ordinal "
                    f"must be an integer >= 0, got {token!r}"
                )
            current.point = ordinal
            index += 1
            continue
        index += 1
    if not sections:
        return ()
    endpoints: list[NativePathEndpoint] = []
    for section in sections:
        if section.rows is None:
            raise ValueError(
                "native_input_error: Gaussian IRC "
                f"{section.direction} section has no geometry block"
            )
        if len(section.rows) != len(expected):
            raise ValueError(
                "native_input_error: Gaussian IRC "
                f"{section.direction} geometry has {len(section.rows)} atoms "
                f"but {len(expected)} were expected"
            )
        symbols = tuple(row[0] for row in section.rows)
        if symbols != expected:
            raise ValueError(
                "native_input_error: Gaussian IRC "
                f"{section.direction} geometry symbols {list(symbols)} "
                f"disagree with expected {list(expected)}"
            )
        coordinates = tuple((row[1], row[2], row[3]) for row in section.rows)
        endpoints.append(
            NativePathEndpoint(
                direction=section.direction,
                geometry=ParsedGeometry(atoms=symbols, coordinates=coordinates),
                point_ordinal=section.point,
                energy_hartree=section.energy,
                converged=section.converged,
            )
        )
    return tuple(endpoints)


def irc_trajectory_facts(log_text: str) -> dict[str, Any]:
    """Summarize IRC trajectory progress without ever raising on content.

    A lenient scan over the module dialect: closed geometry blocks are
    counted per direction, finite endpoint energies are collected in
    encounter order, and any imperfection (a marker outside a section, a
    duplicate banner, an unparsable energy, a malformed convergence or
    point flag, an empty or section-less banner, a second block in one
    section, or a block left unclosed at end of file) sets
    ``"truncated": True`` instead of raising. A point is counted only
    when its block is closed by a subsequent line, so counts never
    include a possibly-partial trailing block.

    Parameters
    ----------
    log_text : str
        Log content in the module dialect (handcrafted fixtures only).

    Returns
    -------
    dict[str, Any]
        ``{"forward_points": int, "reverse_points": int,
        "energies_hartree": [...], "units": "angstrom",
        "truncated": bool, "directions": [...]}`` with ``directions`` in
        first-appearance order.

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
    truncated = False
    banners = {"forward": 0, "reverse": 0}
    blocks = {"forward": 0, "reverse": 0}
    current: str | None = None
    current_blocks = 0
    lines = log_text.splitlines()
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        try:
            direction = _banner_direction(stripped)
        except ValueError:
            truncated = True
            current = None
            index += 1
            continue
        if direction is not None:
            banners[direction] += 1
            if banners[direction] > 1:
                truncated = True
            if direction not in directions:
                directions.append(direction)
            current = direction
            current_blocks = 0
            index += 1
            continue
        if stripped == GEOMETRY_HEADER:
            if current is None:
                truncated = True
                index += 1
                continue
            rows = 0
            index += 1
            while index < len(lines) and _parse_atom_row(lines[index]) is not None:
                rows += 1
                index += 1
            closed = index < len(lines)
            current_blocks += 1
            if current_blocks > 1:
                truncated = True
            if rows == 0:
                truncated = True
            elif not closed:
                truncated = True
            else:
                points[current] += 1
                blocks[current] += 1
            continue
        if stripped.startswith(ENERGY_PREFIX):
            if current is None:
                truncated = True
            else:
                value = _finite_float(stripped[len(ENERGY_PREFIX) :].strip())
                if value is None:
                    truncated = True
                else:
                    energies.append(value)
            index += 1
            continue
        if stripped.startswith(CONVERGED_PREFIX):
            if current is None:
                truncated = True
            elif stripped[len(CONVERGED_PREFIX) :].strip().upper() not in ("YES", "NO"):
                truncated = True
            index += 1
            continue
        if stripped.startswith(POINT_PREFIX):
            if current is None:
                truncated = True
            else:
                token = stripped[len(POINT_PREFIX) :].strip()
                try:
                    ordinal = int(token)
                except (TypeError, ValueError):
                    ordinal = -1
                if ordinal < 0:
                    truncated = True
            index += 1
            continue
        index += 1
    for direction_name in ("forward", "reverse"):
        if banners[direction_name] > blocks[direction_name]:
            truncated = True
    return {
        "forward_points": points["forward"],
        "reverse_points": points["reverse"],
        "energies_hartree": energies,
        "units": "angstrom",
        "truncated": truncated,
        "directions": directions,
    }
