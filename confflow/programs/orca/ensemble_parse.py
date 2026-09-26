#!/usr/bin/env python3

"""ORCA GOAT ensemble-member parsing for the V4 program adapter.

Pure parser over real ORCA 6.1 GOAT output, verified against an
installed-binary butane/HF-3c GOAT run: the log carries a
``# Final ensemble info #`` table (conformer index, relative kcal/mol
energy, degeneracy, populations) plus a ``Lowest energy conformer:
<E> Eh`` line, and member geometries live in the
``<job>.finalensemble.xyz`` file (standard multi-structure XYZ whose
comment lines read ``<energy> converged=<bool>``).  Member identity is
the table/file ordinal (both must agree 0..n-1); parser encounter order
is never an identity beyond that.  Anything else is ignored or fails
closed — never inferred.
"""
from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Any, Final

from ...domain.elements import canonical_element_symbol
from ...execution.native import NativeEnsembleMember, ParsedGeometry

__all__ = [
    "ensemble_energy_table",
    "goat_trajectory_facts",
    "member_ordering",
    "parse_goat_ensemble",
]

#: kcal/mol per Hartree (CODATA 2018) for table-relative energies.
_KCAL_PER_HARTREE: Final[float] = 627.5094740631

#: Real ``# Final ensemble info #`` table rows:
#: ``<index> <rel_kcal> <degen> <%total> <%cumul>``.
_TABLE_ROW_RE = re.compile(
    r"^\s*(\d+)\s+(\S+)\s+(\d+)\s+(\S+)\s+(\S+)\s*$"
)

#: Real lowest-energy line: ``Lowest energy conformer    : <float> Eh``.
_LOWEST_ENERGY_RE = re.compile(r"Lowest energy conformer\s*:\s*(\S+)\s*Eh")

#: Tolerance (Hartree) between table-derived and XYZ-comment energies.
_ENERGY_AGREEMENT_TOLERANCE: Final[float] = 1e-3


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


def _parse_table_rows(log_text: str) -> tuple[list[tuple[int, float, int]], float]:
    """Parse the real final-ensemble table plus the lowest-energy line.

    Returns ``([(index, rel_kcal, degeneracy)], lowest_hartree)``.
    Indices must run ``0..n-1`` in order; anything else fails closed.
    """
    rows: list[tuple[int, float, int]] = []
    in_table = False
    for raw_line in log_text.splitlines():
        line = raw_line.strip()
        if line == "# Final ensemble info #":
            in_table = True
            continue
        if not in_table:
            continue
        if not line or line.startswith("Conformer") or line.startswith("(kcal/mol)") or set(line) <= {"-", " "}:
            continue
        match = _TABLE_ROW_RE.match(line)
        if match is None:
            break
        try:
            index, rel_kcal, degeneracy = (
                int(match.group(1)),
                float(match.group(2)),
                int(match.group(3)),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"native_parse_error: malformed GOAT ensemble table row: {line!r}"
            ) from exc
        if not math.isfinite(rel_kcal):
            raise ValueError(
                f"native_parse_error: non-finite GOAT ensemble energy: {line!r}"
            )
        rows.append((index, rel_kcal, degeneracy))
    if [index for index, _rel, _degen in rows] != list(range(len(rows))):
        raise ValueError(
            "native_parse_error: GOAT ensemble table indices must run 0..n-1 in order"
        )
    lowest_match = _LOWEST_ENERGY_RE.search(log_text)
    if lowest_match is None:
        raise ValueError("native_parse_error: GOAT log carries no lowest-energy conformer line")
    try:
        lowest = float(lowest_match.group(1))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "native_parse_error: malformed GOAT lowest-energy line: "
            f"{lowest_match.group(0)!r}"
        ) from exc
    if not math.isfinite(lowest):
        raise ValueError("native_parse_error: non-finite GOAT lowest energy")
    return rows, lowest


def _parse_ensemble_xyz(
    xyz_text: str, *, expected_count: int
) -> list[tuple[list[str], list[tuple[float, float, float]], float | None]]:
    """Parse real multi-structure GOAT ensemble XYZ text.

    Returns per-block ``(symbols, coordinates, comment_energy)`` in file
    order.  The block count must equal ``expected_count``; each block
    must carry exactly ``len(atoms)`` rows checked by the caller.
    """
    lines = xyz_text.splitlines()
    blocks: list[tuple[list[str], list[tuple[float, float, float]], float | None]] = []
    index = 0
    while index < len(lines):
        while index < len(lines) and not lines[index].strip():
            index += 1
        if index >= len(lines):
            break
        try:
            count = int(lines[index].strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"native_parse_error: GOAT ensemble XYZ block has no atom count: "
                f"{lines[index]!r}"
            ) from exc
        if count < 1:
            raise ValueError("native_parse_error: GOAT ensemble XYZ block is empty")
        if index + 1 >= len(lines):
            raise ValueError("native_parse_error: GOAT ensemble XYZ block lacks a comment line")
        comment = lines[index + 1].strip()
        comment_energy: float | None = None
        if comment:
            try:
                candidate = float(comment.split()[0])
            except (TypeError, ValueError, IndexError):
                candidate = math.nan
            if math.isfinite(candidate):
                comment_energy = candidate
        rows = lines[index + 2 : index + 2 + count]
        if len(rows) != count:
            raise ValueError(
                "native_parse_error: GOAT ensemble XYZ block holds "
                f"{len(rows)} rows for {count} atoms"
            )
        symbols: list[str] = []
        coordinates: list[tuple[float, float, float]] = []
        for row in rows:
            parsed = _parse_coord_line(row)
            if parsed is None:
                raise ValueError(
                    f"native_parse_error: malformed GOAT ensemble coordinate: {row!r}"
                )
            symbols.append(parsed[0])
            coordinates.append((parsed[1], parsed[2], parsed[3]))
        blocks.append((symbols, coordinates, comment_energy))
        index += 2 + count
    if len(blocks) != expected_count:
        raise ValueError(
            "native_parse_error: GOAT ensemble XYZ holds "
            f"{len(blocks)} members for {expected_count} table rows"
        )
    return blocks


def parse_goat_ensemble(
    log_text: str, *, ensemble_xyz_text: str, atoms: Sequence[str]
) -> tuple[NativeEnsembleMember, ...]:
    """Parse GOAT conformer members from real ORCA 6.1 output.

    Member identity is the table/file ordinal (both must agree);
    energies are the XYZ comment absolutes cross-checked against the
    table (lowest plus relative kcal/mol) within 1e-3 Hartree.  Table
    degeneracy/population facts ride in native metadata; identical
    geometries are never deduplicated.

    Parameters
    ----------
    log_text : str
        Full ORCA log content carrying the final-ensemble table.
    ensemble_xyz_text : str
        Content of the ``<job>.finalensemble.xyz`` file.
    atoms : Sequence[str]
        Expected element symbols in atom order; every member geometry
        must carry exactly these symbols in this order.

    Returns
    -------
    tuple[NativeEnsembleMember, ...]
        Parsed members in ordinal order.

    Raises
    ------
    TypeError
        Raised when inputs are not strings.
    ValueError
        Raised on any missing/mismatched real-output fact.
    """
    if not isinstance(log_text, str):
        raise TypeError("GOAT log text must be a string")
    if not isinstance(ensemble_xyz_text, str):
        raise TypeError("GOAT ensemble XYZ text must be a string")
    expected = tuple(atoms)
    if not expected:
        raise ValueError("native_parse_error: GOAT expected atoms must not be empty")
    rows, lowest = _parse_table_rows(log_text)
    if not rows:
        return ()
    blocks = _parse_ensemble_xyz(ensemble_xyz_text, expected_count=len(rows))
    members: list[NativeEnsembleMember] = []
    for ordinal, ((index, rel_kcal, degeneracy), (symbols, coordinates, comment_energy)) in enumerate(
        zip(rows, blocks)
    ):
        assert index == ordinal
        if tuple(symbols) != expected:
            raise ValueError(
                "native_parse_error: GOAT member "
                f"{ordinal} symbols {symbols} disagree with expected {list(expected)}"
            )
        table_energy = lowest + rel_kcal / _KCAL_PER_HARTREE
        if comment_energy is None:
            raise ValueError(
                f"native_parse_error: GOAT member {ordinal} XYZ comment carries no energy"
            )
        if abs(comment_energy - table_energy) > _ENERGY_AGREEMENT_TOLERANCE:
            raise ValueError(
                f"native_parse_error: GOAT member {ordinal} file energy "
                f"{comment_energy!r} disagrees with table energy {table_energy!r}"
            )
        members.append(
            NativeEnsembleMember(
                member_index=ordinal,
                geometry=ParsedGeometry(atoms=tuple(symbols), coordinates=tuple(coordinates)),
                energy_hartree=comment_energy,
                metadata={
                    "parser": "confflow.program.orca.goat_ensemble.v1",
                    "degeneracy": degeneracy,
                    "relative_kcal_per_mol": rel_kcal,
                },
            )
        )
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

    A coarse scan over real ORCA 6.1 markers: the final-ensemble table
    rows plus the lowest-energy line; never raises on content (this is
    a coarse scan, not a substitute for :func:`parse_goat_ensemble`).

    Parameters
    ----------
    text : str
        Full log content, possibly truncated; never raises on content.

    Returns
    -------
    dict[str, Any]
        ``parser`` (this module's format tag), ``member_count`` (table
        rows), ``member_indices`` (0..n-1 when a table parsed),
        ``energy_min_hartree`` / ``energy_max_hartree`` (table-derived
        absolutes, ``None`` when unparseable), and ``truncated``
        (whether the lowest-energy line is absent).

    Raises
    ------
    TypeError
        Raised when ``text`` is not a string.
    """
    if not isinstance(text, str):
        raise TypeError("GOAT trajectory text must be a string")
    try:
        rows, lowest = _parse_table_rows(text)
    except ValueError:
        rows, lowest = [], None
    energies = (
        [lowest + rel / _KCAL_PER_HARTREE for _index, rel, _degen in rows]
        if lowest is not None
        else []
    )
    return {
        "parser": "confflow.program.orca.goat_ensemble.v1",
        "member_count": len(rows),
        "member_indices": list(range(len(rows))),
        "energy_min_hartree": min(energies) if energies else None,
        "energy_max_hartree": max(energies) if energies else None,
        "truncated": lowest is None,
    }
