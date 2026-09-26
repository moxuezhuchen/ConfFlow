#!/usr/bin/env python3

"""ORCA nudged-elastic-band (NEB) helpers for ConfFlow Workflow V4.

This module renders ``%neb`` blocks and parses NEB images plus the NEB-TS
candidate from a documented minimal output dialect.  It owns V4-native copies
of small parsing idioms (it never imports the legacy calc runtime, the
program adapter, or the sibling rendering/parsing helpers) and imports only
:mod:`confflow.execution.native`, :mod:`confflow.domain`, and the standard
library.

Supported native keys for :func:`render_neb_blocks`
----------------------------------------------------
``n_images`` (required ``int`` >= 3, rendered as ``NImages``) and ``neb_ts``
(optional ``bool``, default ``False``) are the only accepted keys.  Any other
key raises ``ValueError`` with a ``native_input_error: ... unsupported ...``
message and is never passed through.  ``NEB_End_XYZFile`` is set from the
``product_xyz_name`` argument, never from native keys.

``neb_ts`` records NEB-TS intent: when true, a ``#`` comment notes that the
job must run under the NEB-TS keyword.  The comment is not a native
directive.  The TS-candidate role applies ONLY when the output explicitly
reports an optimized transition state (see :func:`parse_neb_ts_candidate`):
the highest-energy image of a plain NEB path is never a TS candidate.

Capability boundary
-------------------
Only ``NImages`` and ``NEB_End_XYZFile`` are emitted because those are the
``%neb`` keys established for this implementation (``%neb`` blocks drive NEB
jobs; NEB-TS jobs optimize the highest image toward a transition state).
Every other ``%neb`` tuning key (pre-optimization switches, climbing-image
options, reparameterization controls, iteration caps, and so on) is rejected
as unsupported rather than guessed, because zero legacy ORCA NEB syntax
exists in this repository to port and exact key spellings vary across ORCA
versions.

Minimal output dialect for :func:`parse_neb_images`
----------------------------------------------------
Image identity comes from explicit native ordinals only and is never taken
from encounter order::

    CONFFLOW NEB IMAGE 2 OF 3
    ENERGY -76.100000
    GEOMETRY
    O 0.000000 0.000000 0.000000
    END GEOMETRY

Ordinals are 1-based (``1`` .. ``n_images``); the ``OF <total>`` count must
equal the requested ``n_images``.  Each image section requires exactly one
``GEOMETRY`` ... ``END GEOMETRY`` block whose atom count and symbols must
match the expected ``atoms``; ``ENERGY <float>`` is optional and at most one
per image (absent energy parses as ``None``, never zero).  Blank lines are
ignored; any other line inside a section raises ``ValueError``, as does any
non-blank line outside image sections (other than the skipped NEB-TS
section).  A missing
image, a duplicated image number, or an out-of-range ordinal raises
``ValueError``.  Members are returned sorted by ``member_index``.  The NEB-TS
section, when present in the same text, is skipped here and parsed only by
:func:`parse_neb_ts_candidate`.

Minimal output dialect for :func:`parse_neb_ts_candidate`
----------------------------------------------------------
A member is returned ONLY when the text carries the explicit banner::

    CONFFLOW NEB-TS OPTIMIZED TRANSITION STATE
    ENERGY -76.050000
    GEOMETRY
    O 0.000000 0.000000 0.100000
    END GEOMETRY

``ENERGY`` is optional; the ``GEOMETRY`` block is required (a banner without
a parseable geometry raises ``ValueError``).  A duplicated banner raises
``ValueError``.  Text without the banner yields ``None`` -- in particular, a
path-maximum image without the banner is never returned here.  The candidate
carries the deterministic fallback ``member_index`` 0, flagged in metadata,
because the dialect assigns no native ordinal to the optimized TS.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from ...domain._immutable import FrozenDict
from ...domain.elements import canonical_element_symbol
from ...execution.native import NativeEnsembleMember, ParsedGeometry

__all__ = [
    "NEB_IMAGE_BANNER_FORMAT",
    "NEB_TS_BANNER",
    "SUPPORTED_NEB_KEYS",
    "parse_neb_images",
    "parse_neb_ts_candidate",
    "render_neb_blocks",
]

#: Format template for per-image banners; filled as ``FORMAT.format(i, n)``.
NEB_IMAGE_BANNER_FORMAT: str = "CONFFLOW NEB IMAGE {0} OF {1}"

#: Banner reporting an explicitly optimized NEB-TS transition state.
NEB_TS_BANNER: str = "CONFFLOW NEB-TS OPTIMIZED TRANSITION STATE"

#: Exhaustive allowlist of native keys accepted by :func:`render_neb_blocks`.
SUPPORTED_NEB_KEYS: frozenset[str] = frozenset({"n_images", "neb_ts"})

#: Minimum image count accepted for an NEB path (reactant, TS region, product).
MIN_NEB_IMAGES: int = 3

_GEOMETRY_BEGIN: str = "GEOMETRY"
_GEOMETRY_END: str = "END GEOMETRY"

_NEB_IMAGE_PATTERN = re.compile(r"^CONFFLOW NEB IMAGE\s+(\d+)\s+OF\s+(\d+)\s*$")

#: Parsed section facts: ``((atoms, coordinates), energy_or_None)``.
_GeometryEnergy = tuple[tuple[tuple[str, ...], tuple[tuple[float, ...], ...]], float | None]


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


def _check_product_xyz_name(product_xyz_name: str) -> str:
    """Validate the product XYZ file name for ``NEB_End_XYZFile``.

    Parameters
    ----------
    product_xyz_name : str
        Product structure file name referenced by the ``%neb`` block.

    Returns
    -------
    str
        Stripped file name.

    Raises
    ------
    ValueError
        Raised with a ``native_input_error`` message when the name is empty,
        carries quotes or newlines, or lacks the ``.xyz`` suffix.
    """
    if not isinstance(product_xyz_name, str):
        raise _input_error(
            f"ORCA NEB 'product_xyz_name' must be a string, got {type(product_xyz_name).__name__}"
        )
    name = product_xyz_name.strip()
    if not name:
        raise _input_error("ORCA NEB 'product_xyz_name' must be a non-empty string")
    if '"' in name or "'" in name or "\n" in name or "\r" in name:
        raise _input_error(f"ORCA NEB 'product_xyz_name' carries unsafe characters: {name!r}")
    if not name.endswith(".xyz"):
        raise _input_error(f"ORCA NEB 'product_xyz_name' must end with '.xyz', got {name!r}")
    return name


def render_neb_blocks(native: Mapping[str, Any], *, product_xyz_name: str) -> str:
    """Render the ``%neb`` block from allowlisted native keys.

    Parameters
    ----------
    native : Mapping[str, Any]
        Native option mapping holding ``n_images`` (required ``int`` >= 3)
        and optionally ``neb_ts`` (``bool``).  Any other key is rejected.
    product_xyz_name : str
        Product XYZ file name rendered as ``NEB_End_XYZFile``.

    Returns
    -------
    str
        Rendered ``%neb`` block ending with a newline.  When ``neb_ts`` is
        true, a ``#`` comment records the NEB-TS intent above the block.

    Raises
    ------
    ValueError
        Raised with a ``native_input_error`` message when ``native`` is not
        a mapping, carries unknown keys, ``n_images`` is missing or not an
        integer >= 3, ``neb_ts`` is not a boolean, or ``product_xyz_name``
        is not a safe ``.xyz`` file name.
    """
    if not isinstance(native, Mapping):
        raise _input_error(f"ORCA NEB 'native' must be a mapping, got {type(native).__name__}")
    unknown = sorted(set(native) - set(SUPPORTED_NEB_KEYS))
    if unknown:
        raise _input_error(f"ORCA NEB unsupported native keys: {', '.join(unknown)}")

    if "n_images" not in native:
        raise _input_error("ORCA NEB 'n_images' is required")
    n_images = native["n_images"]
    if isinstance(n_images, bool) or not isinstance(n_images, int) or n_images < MIN_NEB_IMAGES:
        raise _input_error(
            f"ORCA NEB 'n_images' must be an integer >= {MIN_NEB_IMAGES}, got {n_images!r}"
        )

    neb_ts = native.get("neb_ts", False)
    if not isinstance(neb_ts, bool):
        raise _input_error(f"ORCA NEB 'neb_ts' must be a boolean, got {neb_ts!r}")

    name = _check_product_xyz_name(product_xyz_name)
    block = f'%neb\n  NImages {n_images}\n  NEB_End_XYZFile "{name}"\nend\n'
    if neb_ts:
        comment = (
            "# neb_ts true: run with the NEB-TS keyword; the TS-candidate role "
            "applies only if the output explicitly reports an optimized TS\n"
        )
        return comment + block
    return block


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


def _canonical_atoms(atoms: Sequence[str], *, what: str) -> list[str]:
    """Canonicalize and validate an expected atom-symbol sequence.

    Parameters
    ----------
    atoms : Sequence[str]
        Expected element symbols in atom order.
    what : str
        Description used in error messages.

    Returns
    -------
    list[str]
        Canonical element symbols.

    Raises
    ------
    ValueError
        Raised when ``atoms`` is empty or holds an unknown symbol.
    """
    try:
        canonical = [canonical_element_symbol(symbol) for symbol in atoms]
    except (ValueError, ArithmeticError) as exc:
        raise ValueError(f"invalid expected atoms for {what}: {exc}") from exc
    if not canonical:
        raise ValueError(f"expected atoms for {what} must not be empty")
    return canonical


def _parse_geometry_section(
    lines: Sequence[str], *, what: str, allow_energy: bool = True
) -> _GeometryEnergy:
    """Parse section lines into a geometry plus an optional energy.

    Parameters
    ----------
    lines : Sequence[str]
        Raw section lines between banners.
    what : str
        Description used in error messages.
    allow_energy : bool, optional
        Whether an ``ENERGY`` line is accepted (at most one).

    Returns
    -------
    tuple
        ``((atoms, coordinates), energy_or_None)`` parsed facts.

    Raises
    ------
    ValueError
        Raised on missing, duplicated, or malformed lines.
    """
    energy: float | None = None
    energy_hits = 0
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
            # Trailing program termination marker: the termination fact is
            # read separately, never as section data.
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
                raise ValueError(f"{what} has a duplicate GEOMETRY block")
            in_geometry = True
            continue
        if line == _GEOMETRY_END:
            raise ValueError(f"{what} has END GEOMETRY without GEOMETRY")
        parts = line.split(None, 1)
        keyword = parts[0].upper()
        rest = parts[1].strip() if len(parts) == 2 else ""
        if keyword == "ENERGY" and allow_energy:
            energy_hits += 1
            if energy_hits > 1:
                raise ValueError(f"{what} has a duplicate ENERGY line")
            try:
                energy = float(rest)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{what} has invalid energy {rest!r}") from exc
            if not math.isfinite(energy):
                raise ValueError(f"{what} has non-finite energy {rest!r}")
            continue
        raise ValueError(f"{what} has unknown line {raw!r}")

    if in_geometry:
        raise ValueError(f"{what} has an unterminated GEOMETRY block")
    if geometry_hits != 1 or geometry is None:
        raise ValueError(f"{what} requires exactly one GEOMETRY block")
    return geometry, energy


def _check_geometry_atoms(
    parsed_atoms: Sequence[str], expected: Sequence[str], *, what: str
) -> None:
    """Validate parsed atom symbols against the expected sequence.

    Parameters
    ----------
    parsed_atoms : Sequence[str]
        Canonical symbols parsed from the section geometry.
    expected : Sequence[str]
        Expected canonical symbols in atom order.
    what : str
        Description used in error messages.

    Raises
    ------
    ValueError
        Raised on count or symbol mismatches.
    """
    if len(parsed_atoms) != len(expected):
        raise ValueError(f"{what} has {len(parsed_atoms)} atoms, expected {len(expected)}")
    for index, (found, wanted) in enumerate(zip(parsed_atoms, expected)):
        if found != wanted:
            raise ValueError(f"{what} atom {index} is {found!r}, expected {wanted!r}")


def parse_neb_images(
    text: str, *, atoms: Sequence[str], n_images: int
) -> tuple[NativeEnsembleMember, ...]:
    """Parse NEB image members keyed by explicit native image ordinals.

    Parameters
    ----------
    text : str
        Full log file content in the minimal NEB dialect.
    atoms : Sequence[str]
        Expected element symbols in atom order; every image geometry must
        match both the count and the symbols.
    n_images : int
        Expected image count; the parsed member count must equal it.

    Returns
    -------
    tuple[NativeEnsembleMember, ...]
        Members sorted by ``member_index`` (the native image number, never
        the parser encounter order).  Images without an ``ENERGY`` line
        carry ``None`` energy, never zero.

    Raises
    ------
    ValueError
        Raised when ``n_images`` is not a positive integer, a banner total
        disagrees with ``n_images``, an ordinal is out of range or
        duplicated, the member count differs from ``n_images``, or a section
        is malformed or disagrees with ``atoms``.
    """
    if isinstance(n_images, bool) or not isinstance(n_images, int) or n_images < 1:
        raise ValueError(f"n_images must be a positive integer, got {n_images!r}")
    expected = _canonical_atoms(atoms, what="NEB images")

    sections: dict[int, list[str]] = {}
    current: int | None = None
    in_ts_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if line == NEB_TS_BANNER:
            in_ts_section = True
            current = None
            continue
        match = _NEB_IMAGE_PATTERN.match(line)
        if match is not None:
            in_ts_section = False
            ordinal = int(match.group(1))
            total = int(match.group(2))
            if total != n_images:
                raise ValueError(
                    f"NEB image banner total {total} disagrees with n_images {n_images}"
                )
            if ordinal < 1 or ordinal > n_images:
                raise ValueError(f"NEB image ordinal {ordinal} out of range 1..{n_images}")
            if ordinal in sections:
                raise ValueError(f"NEB image {ordinal} appears more than once")
            sections[ordinal] = []
            current = ordinal
            continue
        if in_ts_section or current is None:
            if not line:
                continue
            if current is None and not in_ts_section:
                raise ValueError(f"line outside NEB image sections: {raw!r}")
            continue
        sections[current].append(raw)

    if len(sections) != n_images:
        missing = sorted(set(range(1, n_images + 1)) - set(sections))
        raise ValueError(f"NEB image count {len(sections)} != n_images {n_images}; {missing}")

    members: list[NativeEnsembleMember] = []
    for ordinal in sorted(sections):
        what = f"NEB image {ordinal}"
        (parsed_atoms, parsed_coords), energy = _parse_geometry_section(
            sections[ordinal], what=what
        )
        _check_geometry_atoms(parsed_atoms, expected, what=what)
        members.append(
            NativeEnsembleMember(
                member_index=ordinal,
                geometry=ParsedGeometry(
                    atoms=tuple(parsed_atoms),
                    coordinates=tuple(tuple(point) for point in parsed_coords),
                ),
                energy_hartree=energy,
                metadata=FrozenDict({"neb_total": n_images}),
            )
        )
    return tuple(members)


def parse_neb_ts_candidate(text: str) -> NativeEnsembleMember | None:
    """Parse the explicitly reported NEB-TS optimized transition state.

    Parameters
    ----------
    text : str
        Full log file content in the minimal NEB dialect.

    Returns
    -------
    NativeEnsembleMember | None
        The TS-candidate member, or ``None`` when the text carries no
        ``CONFFLOW NEB-TS OPTIMIZED TRANSITION STATE`` banner.  A
        path-maximum image without the banner never yields a member here.

    Raises
    ------
    ValueError
        Raised when the banner is duplicated or its section is malformed.
    """
    hits = [line for line in text.splitlines() if line.strip() == NEB_TS_BANNER]
    if not hits:
        return None
    if len(hits) != 1:
        raise ValueError("duplicate NEB-TS banner: the candidate section must be unique")
    section: list[str] = []
    in_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if line == NEB_TS_BANNER:
            in_section = True
            continue
        if not in_section:
            continue
        if _NEB_IMAGE_PATTERN.match(line) is not None:
            break
        section.append(raw)
    (parsed_atoms, parsed_coords), energy = _parse_geometry_section(
        section, what="NEB-TS candidate"
    )
    if not parsed_atoms:
        raise ValueError("NEB-TS candidate geometry must not be empty")
    return NativeEnsembleMember(
        member_index=0,
        geometry=ParsedGeometry(
            atoms=tuple(parsed_atoms),
            coordinates=tuple(tuple(point) for point in parsed_coords),
        ),
        energy_hartree=energy,
        metadata=FrozenDict({"neb_ts_candidate": True, "member_index_fallback": True}),
    )
