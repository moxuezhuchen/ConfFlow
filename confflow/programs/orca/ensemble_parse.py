#!/usr/bin/env python3

"""ORCA GOAT ensemble-member parsing for the V4 program adapter.

Pure parser for the crafted strict dialect ``confflow-goat-v1``.  The
dialect is defined here, used by crafted fixtures and the V4-5 fake-E2E
path only; it is NOT native ORCA output and makes no claim to reproduce
any ORCA release's GOAT report format.

Dialect ``confflow-goat-v1`` (blank lines are ignored everywhere):

- Each member opens with exactly one banner line::

    GOAT CONFORMER <member_index>

  where ``member_index`` is a non-negative integer.  The banner number is
  the member identity (``NativeEnsembleMember.member_index``); parser
  encounter order is never used as an identity.
- The banner is followed by one or more coordinate lines::

    <symbol> <x> <y> <z>

- At most one energy line may appear anywhere inside the member section::

    Conformer energy: <float> Hartree

  A missing energy line means "unknown" and parses to ``None``, never
  ``0.0``.
- Any other non-blank line (text before the first banner, prose inside a
  member section, a second energy line, a banner with no coordinates)
  fails closed with ``ValueError``.
- Member sections run to the next banner or end of text.  Identical
  geometries are never deduplicated: two banners with the same coordinates
  yield two members with distinct indices.
- Empty or whitespace-only text parses to an empty tuple; the ensemble
  result profile fails closed downstream on the empty set, not this parser.

Deterministic ordering rule: :func:`parse_goat_members` preserves text
encounter order; :func:`member_ordering` reports sorted member indices for
callers that need a canonical order.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Any

from ...domain.elements import canonical_element_symbol
from ...execution.native import NativeEnsembleMember, ParsedGeometry

__all__ = [
    "GOAT_DIALECT_VERSION",
    "ensemble_energy_table",
    "goat_trajectory_facts",
    "member_ordering",
    "parse_goat_members",
]

#: Version tag of the crafted strict dialect parsed here.
GOAT_DIALECT_VERSION: str = "confflow-goat-v1"

_BANNER_RE = re.compile(r"GOAT CONFORMER (\d+)")
_ENERGY_RE = re.compile(
    r"Conformer energy:\s*" r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)" r"\s*Hartree"
)
_TRUNCATION_RE = re.compile(r"truncat", re.IGNORECASE)


def _parse_coord_line(line: str) -> tuple[str, float, float, float] | None:
    """Parse one ``<symbol> <x> <y> <z>`` dialect line.

    Parameters
    ----------
    line : str
        Candidate coordinate line (already stripped).

    Returns
    -------
    tuple[str, float, float, float] | None
        Canonical ``(symbol, x, y, z)`` triple, or ``None`` when the line
        is not a well-formed coordinate line.
    """
    parts = line.split()
    if len(parts) != 4:
        return None
    try:
        symbol = canonical_element_symbol(parts[0])
        point = (float(parts[1]), float(parts[2]), float(parts[3]))
    except (ValueError, ArithmeticError):
        return None
    if not all(math.isfinite(value) for value in point):
        return None
    return symbol, point[0], point[1], point[2]


def _parse_energy_token(token: str, *, line: str) -> float:
    """Convert an energy token to a finite float.

    Parameters
    ----------
    token : str
        Raw numeric token captured from an energy line.
    line : str
        Full line used in error messages.

    Returns
    -------
    float
        Finite energy in Hartree.

    Raises
    ------
    ValueError
        Raised when the token is not a finite number.
    """
    try:
        value = float(token)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"native_parse_error: invalid GOAT conformer energy in line {line!r}"
        ) from exc
    if not math.isfinite(value):
        raise ValueError(f"native_parse_error: non-finite GOAT conformer energy in line {line!r}")
    return value


def parse_goat_members(text: str, *, atoms: Sequence[str]) -> tuple[NativeEnsembleMember, ...]:
    """Parse crafted-dialect GOAT conformer members from log text.

    Parameters
    ----------
    text : str
        Full log content in the ``confflow-goat-v1`` dialect.
    atoms : Sequence[str]
        Expected atoms; each member geometry must carry exactly
        ``len(atoms)`` coordinates (symbols are validated as elements but
        are not required to match ``atoms`` entry-wise).

    Returns
    -------
    tuple[NativeEnsembleMember, ...]
        Parsed members in text encounter order with ``member_index`` taken
        from each banner number and missing energies as ``None``.

    Raises
    ------
    TypeError
        Raised when ``text`` is not a string.
    ValueError
        Raised on duplicate banner numbers, geometry atom-count mismatch,
        banners without coordinates, repeated energy lines, or any other
        non-blank line outside the dialect grammar.
    """
    if not isinstance(text, str):
        raise TypeError("GOAT member text must be a string")
    if not text.strip():
        return ()
    expected_count = len(tuple(atoms))

    members: list[NativeEnsembleMember] = []
    seen: set[int] = set()
    current_index: int | None = None
    current_atoms: list[str] = []
    current_coords: list[tuple[float, float, float]] = []
    current_energy: float | None = None
    energy_seen = False

    def _flush() -> None:
        if current_index is None:
            return
        if not current_coords:
            raise ValueError(
                "native_parse_error: " f"GOAT CONFORMER {current_index} carries no coordinates"
            )
        if len(current_coords) != expected_count:
            raise ValueError(
                "native_parse_error: GOAT CONFORMER "
                f"{current_index} atom count ({len(current_coords)}) "
                f"!= expected ({expected_count})"
            )
        geometry = ParsedGeometry(
            atoms=tuple(current_atoms),
            coordinates=tuple(current_coords),
        )
        members.append(
            NativeEnsembleMember(
                member_index=current_index,
                geometry=geometry,
                energy_hartree=current_energy,
                metadata={"dialect": GOAT_DIALECT_VERSION},
            )
        )

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line == "****ORCA TERMINATED NORMALLY****":
            # Trailing program termination marker: the termination fact is
            # read separately, never as member data.
            continue
        banner = _BANNER_RE.fullmatch(line)
        if banner is not None:
            _flush()
            current_index = int(banner.group(1))
            if current_index in seen:
                raise ValueError("native_parse_error: " f"duplicate GOAT CONFORMER {current_index}")
            seen.add(current_index)
            current_atoms = []
            current_coords = []
            current_energy = None
            energy_seen = False
            continue
        if current_index is None:
            raise ValueError(f"native_parse_error: GOAT text before first banner: {line!r}")
        energy = _ENERGY_RE.fullmatch(line)
        if energy is not None:
            if energy_seen:
                raise ValueError(
                    "native_parse_error: " f"repeated energy line in GOAT CONFORMER {current_index}"
                )
            current_energy = _parse_energy_token(energy.group(1), line=line)
            energy_seen = True
            continue
        parsed = _parse_coord_line(line)
        if parsed is None:
            raise ValueError(
                "native_parse_error: "
                f"unexpected line in GOAT CONFORMER {current_index}: {line!r}"
            )
        symbol, x, y, z = parsed
        current_atoms.append(symbol)
        current_coords.append((x, y, z))
    _flush()
    return tuple(members)


def ensemble_energy_table(
    members: Sequence[NativeEnsembleMember],
) -> dict[int, float | None]:
    """Map member indices to their energies.

    Parameters
    ----------
    members : Sequence[NativeEnsembleMember]
        Parsed ensemble members.

    Returns
    -------
    dict[int, float | None]
        ``member_index`` to ``energy_hartree`` (``None`` when unknown).

    Raises
    ------
    ValueError
        Raised when two members share a ``member_index``.
    """
    table: dict[int, float | None] = {}
    for member in members:
        if member.member_index in table:
            raise ValueError(
                "native_parse_error: " f"duplicate ensemble member_index {member.member_index}"
            )
        table[member.member_index] = member.energy_hartree
    return table


def member_ordering(
    members: Sequence[NativeEnsembleMember],
) -> tuple[int, ...]:
    """Return the canonical deterministic ordering of member indices.

    Parameters
    ----------
    members : Sequence[NativeEnsembleMember]
        Parsed ensemble members.

    Returns
    -------
    tuple[int, ...]
        Sorted member indices, regardless of parser encounter order.
    """
    return tuple(sorted(member.member_index for member in members))


def goat_trajectory_facts(text: str) -> dict[str, Any]:
    """Scan log text for coarse GOAT trajectory facts (best-effort).

    Parameters
    ----------
    text : str
        Full log content, possibly truncated or off-dialect; never raises
        on content (this is a coarse regex scan, not a substitute for
        :func:`parse_goat_members`).

    Returns
    -------
    dict[str, Any]
        ``dialect`` (this module's dialect tag), ``member_count`` (banner
        occurrences), ``member_indices`` (sorted unique banner numbers),
        ``energy_min_hartree`` / ``energy_max_hartree`` (``None`` when no
        finite energy line scanned), and ``truncated`` (whether the text
        mentions truncation, case-insensitive).

    Raises
    ------
    TypeError
        Raised when ``text`` is not a string.
    """
    if not isinstance(text, str):
        raise TypeError("GOAT trajectory text must be a string")
    indices = [int(token) for token in _BANNER_RE.findall(text)]
    energies: list[float] = []
    for token in _ENERGY_RE.findall(text):
        try:
            value = float(token)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            energies.append(value)
    return {
        "dialect": GOAT_DIALECT_VERSION,
        "member_count": len(indices),
        "member_indices": sorted(set(indices)),
        "energy_min_hartree": min(energies) if energies else None,
        "energy_max_hartree": max(energies) if energies else None,
        "truncated": _TRUNCATION_RE.search(text) is not None,
    }
