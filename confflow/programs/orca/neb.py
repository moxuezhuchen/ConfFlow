#!/usr/bin/env python3

"""ORCA nudged-elastic-band (NEB) helpers for ConfFlow Workflow V4.

This module renders ``%neb`` blocks and parses NEB images plus the NEB-TS
candidate from real ORCA 6.1 output, verified against an installed-binary
HCN isomerization NEB run (HF-3c, 5 images): the log announces trajectory
files (``Current trajectory will be written to <base>_MEP_trj.xyz``) and
reports an ``INFORMATION ABOUT HIGHEST ENERGY IMAGE`` block (image number,
``Energy ... Eh``, ``HIGHEST ENERGY IMAGE (ANGSTROEM)`` coordinates); member
geometries and energies live in the ``<base>_MEP_trj.xyz`` multi-structure
file (standard XYZ with ``Coordinates from ORCA-job ... E <float>``
comments, endpoints included).  Member identity is file order (0-based);
the highest-energy image number is the native TS-candidate identity.  It
owns V4-native copies of small parsing idioms (it never imports the legacy
calc runtime, the program adapter, or the sibling rendering/parsing
helpers) and imports only :mod:`confflow.execution.native`,
:mod:`confflow.domain`, and the standard library.

Supported native keys for :func:`render_neb_blocks`
----------------------------------------------------
``n_images`` (required ``int`` >= 3, rendered as ``NImages``) and ``neb_ts``
(optional ``bool``, default ``False``) are the only accepted keys.  Any other
key raises ``ValueError`` with a ``native_input_error: ... unsupported ...``
message and is never passed through.  ``NEB_End_XYZFile`` is set from the
``product_xyz_name`` argument, never from native keys.  The caller (program
adapter) selects the ``NEB`` / ``NEB-TS`` job keyword: the block alone
under a plain keyword runs a different job silently (verified), so
keyword/mode consistency is enforced at render and compile time.

Real output grammar for :func:`parse_neb_images`
-------------------------------------------------
Images come from the ``<base>_MEP_trj.xyz`` trajectory file: standard XYZ
blocks (count, energy comment, coordinate rows) in path order, endpoints
included, so exactly ``n_images + 2`` blocks are required.  Energies come
from the comments (required, never zero); atom count and symbols must
match the expected ``atoms`` exactly.  Members are returned in file order
with 0-based ``member_index`` (the native image number, endpoints
included); the adapter assigns the ``neb_image`` role.

Real output grammar for :func:`parse_neb_ts_candidate`
-------------------------------------------------------
The candidate comes ONLY from an explicit native report: a
``Highest energy image .... <int>`` line plus an ``Energy .... <float>
Eh`` line plus a ``HIGHEST ENERGY IMAGE (ANGSTROEM)`` coordinate block
(bare ``SYM x y z`` rows).  The reported image number is the candidate
identity.  Text without the report yields ``None`` — in particular, a
path-maximum image without the report is never returned here.
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
    "SUPPORTED_NEB_KEYS",
    "parse_neb_images",
    "parse_neb_ts_candidate",
    "render_neb_blocks",
]

#: Exhaustive allowlist of native keys accepted by :func:`render_neb_blocks`.
SUPPORTED_NEB_KEYS: frozenset[str] = frozenset({"n_images", "neb_ts"})

#: Minimum image count accepted for an NEB path (reactant, TS region, product).
MIN_NEB_IMAGES: int = 3

#: Real highest-energy-image report markers.
_HEI_NUMBER_RE = re.compile(r"^Highest energy image\s+\.+\s+(\d+)\s*$", re.MULTILINE)
_HEI_ENERGY_RE = re.compile(r"^Energy\s+\.+\s+(\S+)\s*Eh\s*$")
_HEI_BLOCK_HEADER = "HIGHEST ENERGY IMAGE (ANGSTROEM)"

#: Real XYZ energy comment: ``Coordinates from ORCA-job <base> E <float>``.
_XYZ_ENERGY_RE = re.compile(r"^Coordinates from ORCA-job\s+\S+\s+E\s+(\S+)\s*$")

#: Parsed image facts: ``((atoms, coordinates), energy)``.
_GeometryEnergy = tuple[tuple[tuple[str, ...], tuple[tuple[float, ...], ...]], float]


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


def _parse_mep_xyz(
    xyz_text: str, *, expected_blocks: int
) -> list[tuple[list[str], list[tuple[float, float, float]], float]]:
    """Parse a real MEP trajectory XYZ document into image blocks.

    Returns ``[(symbols, coordinates, comment_energy)]`` in file order.
    Block count must equal ``expected_blocks``; every comment must carry
    a finite energy (never zero-by-default); chrome lines never occur
    inside real XYZ blocks, so any structural deviation fails closed.
    """
    lines = xyz_text.splitlines()
    blocks: list[tuple[list[str], list[tuple[float, float, float]], float]] = []
    index = 0
    while index < len(lines):
        while index < len(lines) and not lines[index].strip():
            index += 1
        if index >= len(lines):
            break
        try:
            count = int(lines[index].strip())
        except (TypeError, ValueError) as exc:
            raise _input_error(
                f"NEB trajectory XYZ block has no atom count: {lines[index]!r}"
            ) from exc
        if count < 1:
            raise _input_error("NEB trajectory XYZ block is empty")
        if index + 1 >= len(lines):
            raise _input_error("NEB trajectory XYZ block lacks a comment line")
        comment = lines[index + 1].strip()
        comment_match = _XYZ_ENERGY_RE.match(comment)
        if comment_match is None:
            raise _input_error(
                f"NEB trajectory XYZ comment carries no energy: {comment!r}"
            )
        try:
            energy = float(comment_match.group(1))
        except (TypeError, ValueError) as exc:
            raise _input_error(
                f"NEB trajectory XYZ comment energy is malformed: {comment!r}"
            ) from exc
        if not math.isfinite(energy):
            raise _input_error("NEB trajectory XYZ comment energy is non-finite")
        rows = lines[index + 2 : index + 2 + count]
        if len(rows) != count:
            raise _input_error(
                f"NEB trajectory XYZ block holds {len(rows)} rows for {count} atoms"
            )
        symbols: list[str] = []
        coordinates: list[tuple[float, float, float]] = []
        for row in rows:
            symbol, x, y, z = _parse_coord_line(row)
            symbols.append(symbol)
            coordinates.append((x, y, z))
        blocks.append((symbols, coordinates, energy))
        index += 2 + count
    if len(blocks) != expected_blocks:
        raise _input_error(
            f"NEB trajectory holds {len(blocks)} images for {expected_blocks} expected"
        )
    return blocks


def parse_neb_images(
    mep_xyz_text: str, *, atoms: Sequence[str], n_images: int
) -> tuple[NativeEnsembleMember, ...]:
    """Parse NEB image members from a real MEP trajectory XYZ document.

    Identity is file order (0-based, endpoints included); exactly
    ``n_images + 2`` blocks are required.  Energies come from the XYZ
    comments (required).  Atom count and symbols must match the expected
    ``atoms`` exactly.  Members are returned in file order with the
    ``neb_image`` role.

    Parameters
    ----------
    mep_xyz_text : str
        Content of the ``<job>_MEP_trj.xyz`` trajectory file.
    atoms : Sequence[str]
        Expected element symbols in atom order.
    n_images : int
        Requested intermediate image count; the file must hold exactly
        ``n_images + 2`` blocks (both endpoints included).

    Returns
    -------
    tuple[NativeEnsembleMember, ...]
        Members in file order.

    Raises
    ------
    ValueError
        Raised when ``n_images`` is not a positive integer, the block
        count disagrees, or a block is malformed or disagrees with
        ``atoms``.
    """
    if isinstance(n_images, bool) or not isinstance(n_images, int) or n_images < 1:
        raise _input_error(f"n_images must be a positive integer, got {n_images!r}")
    expected = _canonical_atoms(atoms, what="NEB images")
    blocks = _parse_mep_xyz(mep_xyz_text, expected_blocks=n_images + 2)
    members: list[NativeEnsembleMember] = []
    for ordinal, (symbols, coordinates, energy) in enumerate(blocks):
        _check_geometry_atoms(symbols, expected, what=f"NEB image {ordinal}")
        members.append(
            NativeEnsembleMember(
                member_index=ordinal,
                geometry=ParsedGeometry(
                    atoms=tuple(symbols),
                    coordinates=tuple(tuple(point) for point in coordinates),
                ),
                energy_hartree=energy,
                metadata=FrozenDict({"parser": "confflow.program.orca.neb.v1"}),
            )
        )
    return tuple(members)


def parse_neb_ts_candidate(
    text: str, *, atoms: Sequence[str]
) -> NativeEnsembleMember | None:
    """Parse the explicitly reported NEB highest-energy image.

    The candidate comes ONLY from the native
    ``INFORMATION ABOUT HIGHEST ENERGY IMAGE`` report: the reported
    image number is the candidate identity, ``Energy ... Eh`` is its
    energy, and the ``HIGHEST ENERGY IMAGE (ANGSTROEM)`` coordinate
    block is its geometry.  Text without the report yields ``None`` —
    in particular, a path-maximum image without the report is never
    returned here.

    Parameters
    ----------
    text : str
        Full log file content.
    atoms : Sequence[str]
        Expected element symbols in atom order.

    Returns
    -------
    NativeEnsembleMember | None
        The TS-candidate member with the reported image number, or
        ``None`` when the log carries no highest-energy-image report.

    Raises
    ------
    ValueError
        Raised on a duplicated report, a malformed number/energy, or a
        geometry disagreeing with ``atoms``.
    """
    number_hits = _HEI_NUMBER_RE.findall(text)
    if not number_hits:
        return None
    if len(number_hits) > 1:
        raise _input_error("duplicate highest-energy-image reports: candidate ambiguous")
    lines = text.splitlines()
    number_line = next(
        index for index, line in enumerate(lines) if _HEI_NUMBER_RE.match(line.strip())
    )
    try:
        number = int(number_hits[0])
    except (TypeError, ValueError) as exc:
        raise _input_error("highest-energy-image number is malformed") from exc
    if number < 0:
        raise _input_error("highest-energy-image number must be >= 0")
    energy: float | None = None
    block_start: int | None = None
    for index in range(number_line, len(lines)):
        stripped = lines[index].strip()
        if block_start is None and stripped == _HEI_BLOCK_HEADER:
            block_start = index + 1
            continue
        if energy is None:
            energy_match = _HEI_ENERGY_RE.match(stripped)
            if energy_match is not None:
                try:
                    candidate = float(energy_match.group(1))
                except (TypeError, ValueError) as exc:
                    raise _input_error(
                        "highest-energy-image energy is malformed"
                    ) from exc
                if not math.isfinite(candidate):
                    raise _input_error("highest-energy-image energy is non-finite")
                energy = candidate
    if energy is None:
        raise _input_error("highest-energy-image report carries no energy")
    if block_start is None:
        raise _input_error("highest-energy-image report carries no geometry block")
    symbols: list[str] = []
    coordinates: list[tuple[float, float, float]] = []
    for raw in lines[block_start:]:
        stripped = raw.strip()
        if not stripped or set(stripped) <= {"-"}:
            if coordinates:
                break
            continue
        try:
            symbol, x, y, z = _parse_coord_line(stripped)
        except ValueError:
            break
        symbols.append(symbol)
        coordinates.append((x, y, z))
    if not symbols:
        raise _input_error("highest-energy-image geometry block is empty")
    expected = _canonical_atoms(atoms, what="NEB-TS candidate")
    _check_geometry_atoms(symbols, expected, what="NEB-TS candidate")
    return NativeEnsembleMember(
        member_index=number,
        geometry=ParsedGeometry(
            atoms=tuple(symbols), coordinates=tuple(coordinates)
        ),
        energy_hartree=energy,
        metadata=FrozenDict(
            {"parser": "confflow.program.orca.neb.v1", "neb_ts_candidate": True}
        ),
    )
