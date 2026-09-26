#!/usr/bin/env python3

"""Gaussian QST2/QST3 named-slot rendering helpers for V4-5.

Pure helpers over documented Gaussian QST semantics: a QST2 job carries
exactly two molecule specifications (reactant, product) sharing one
charge/multiplicity pair, and a QST3 job adds a third (transition-state
guess) specification. Atom permutation across slots is owned by the
mapping layer; this renderer owns the count gate and the section text.

Rendered layout (owned by this module, stated exactly): one block per
slot, blocks joined by a single blank line, the whole section ending in
a single newline. Each block is a title card (``reactant``, ``product``,
``guess``), a ``<charge> <multiplicity>`` line, then one
``<symbol> <x> <y> <z>`` line per atom with ``%.8f`` coordinates.
Specifications are always inline; this renderer never emits ``@file``
references or filenames.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Final

from ...domain.elements import canonical_element_symbol
from ...domain.errors import ElementSymbolError
from ...domain.structure import StructureRecord

__all__ = [
    "parse_qst_route",
    "render_qst_molecule_specs",
    "validate_qst_slots",
]

#: Title card of the first molecule specification.
REACTANT_TITLE: Final[str] = "reactant"

#: Title card of the second molecule specification.
PRODUCT_TITLE: Final[str] = "product"

#: Title card of the QST3 transition-state guess specification.
GUESS_TITLE: Final[str] = "guess"

_QST_ITEM_PATTERN: Final = re.compile(r"(?<![A-Za-z0-9])QST([23])(?![0-9])", re.IGNORECASE)


def _resolve_charge(charge: int | None) -> int:
    """Validate the resolved QST total charge.

    Parameters
    ----------
    charge : int | None
        Total charge shared by every QST slot.

    Returns
    -------
    int
        The validated charge.

    Raises
    ------
    ValueError
        Raised as ``native_input_error`` when *charge* is missing or
        not an integer; the renderer never defaults it silently.
    """
    if charge is None:
        raise ValueError(
            "native_input_error: Gaussian QST 'charge' is not resolved (None); "
            "declare the charge explicitly instead of defaulting it"
        )
    if isinstance(charge, bool) or not isinstance(charge, int):
        raise ValueError(
            f"native_input_error: Gaussian QST 'charge' must be an integer, got {charge!r}"
        )
    return charge


def _resolve_multiplicity(multiplicity: int | None) -> int:
    """Validate the resolved QST spin multiplicity.

    Parameters
    ----------
    multiplicity : int | None
        Spin multiplicity shared by every QST slot.

    Returns
    -------
    int
        The validated multiplicity.

    Raises
    ------
    ValueError
        Raised as ``native_input_error`` when *multiplicity* is missing,
        not an integer, or below one.
    """
    if multiplicity is None:
        raise ValueError(
            "native_input_error: Gaussian QST 'multiplicity' is not resolved (None); "
            "declare the multiplicity explicitly instead of defaulting it"
        )
    if isinstance(multiplicity, bool) or not isinstance(multiplicity, int):
        raise ValueError(
            "native_input_error: Gaussian QST 'multiplicity' must be an integer, "
            f"got {multiplicity!r}"
        )
    if multiplicity < 1:
        raise ValueError(
            "native_input_error: Gaussian QST 'multiplicity' must be >= 1, " f"got {multiplicity!r}"
        )
    return multiplicity


def _require_slot(slot: StructureRecord | None, name: str) -> StructureRecord:
    """Return a present QST slot, rejecting missing or mistyped values.

    Parameters
    ----------
    slot : StructureRecord | None
        Candidate slot record.
    name : str
        Slot name used in error messages.

    Returns
    -------
    StructureRecord
        The validated slot.

    Raises
    ------
    ValueError
        Raised as ``native_input_error`` when *slot* is ``None`` or not
        a :class:`~confflow.domain.structure.StructureRecord`.
    """
    if slot is None:
        raise ValueError(f"native_input_error: Gaussian QST {name!r} slot is missing")
    if not isinstance(slot, StructureRecord):
        raise ValueError(
            f"native_input_error: Gaussian QST {name!r} slot must be a "
            f"StructureRecord, got {type(slot).__name__}"
        )
    return slot


def validate_qst_slots(
    *,
    reactant: StructureRecord | None,
    product: StructureRecord | None,
    guess: StructureRecord | None = None,
    charge: int | None,
    multiplicity: int | None,
) -> None:
    """Validate that QST slots agree on charge, multiplicity, and size.

    Gaussian QST requires identical charge and multiplicity across slots.
    A slot that declares no charge/multiplicity inherits the resolved
    values; a slot that declares conflicting values is rejected. Atom
    permutation across slots is owned by the mapping layer; only the
    atom-count gate lives here.

    Parameters
    ----------
    reactant : StructureRecord | None
        Reactant slot; required.
    product : StructureRecord | None
        Product slot; required.
    guess : StructureRecord | None, optional
        Transition-state guess slot; ``None`` means a QST2 shape.
    charge : int | None
        Resolved total charge shared by every slot.
    multiplicity : int | None
        Resolved spin multiplicity shared by every slot.

    Raises
    ------
    ValueError
        Raised as ``native_input_error`` when a required slot is
        missing or mistyped, charge/multiplicity are unresolved, a slot
        declares a conflicting charge/multiplicity, or slot atom counts
        differ.
    """
    reactant_record = _require_slot(reactant, "reactant")
    product_record = _require_slot(product, "product")
    guess_record = None if guess is None else _require_slot(guess, "guess")
    charge_value = _resolve_charge(charge)
    multiplicity_value = _resolve_multiplicity(multiplicity)
    slots = (("reactant", reactant_record), ("product", product_record))
    if guess_record is not None:
        slots = slots + (("guess", guess_record),)
    for name, record in slots:
        if record.charge is not None and record.charge != charge_value:
            raise ValueError(
                f"native_input_error: Gaussian QST {name!r} slot charge "
                f"{record.charge} conflicts with resolved charge {charge_value}"
            )
        if record.multiplicity is not None and record.multiplicity != multiplicity_value:
            raise ValueError(
                "native_input_error: Gaussian QST "
                f"{name!r} slot multiplicity {record.multiplicity} conflicts "
                f"with resolved multiplicity {multiplicity_value}"
            )
    reference = len(reactant_record.atoms)
    for name, record in slots[1:]:
        if len(record.atoms) != reference:
            raise ValueError(
                f"native_input_error: Gaussian QST {name!r} slot has "
                f"{len(record.atoms)} atoms but the reactant slot has "
                f"{reference}; slots must carry equal atom counts"
            )


def _format_coordinate(value: float, title: str, position: int) -> str:
    """Format one coordinate with ``%.8f`` and normalized signed zeros.

    Parameters
    ----------
    value : float
        Coordinate value in Angstrom.
    title : str
        Slot title used in error messages.
    position : int
        1-based atom position used in error messages.

    Returns
    -------
    str
        ``%.8f`` text; ``-0.0`` renders as ``0.00000000`` so output is
        byte-deterministic.

    Raises
    ------
    ValueError
        Raised as ``native_input_error`` when *value* is not a finite
        real number.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            "native_input_error: Gaussian QST "
            f"{title!r} coordinates for atom {position} must be real numbers"
        )
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(
            "native_input_error: Gaussian QST "
            f"{title!r} coordinates for atom {position} must be finite"
        )
    if number == 0:
        number = 0.0
    return f"{number:.8f}"


def _format_spec(
    title: str,
    atoms: Sequence[str],
    coords: Sequence[Sequence[float]],
    charge: int,
    multiplicity: int,
) -> str:
    """Format one QST molecule-specification block.

    Parameters
    ----------
    title : str
        Slot title card (``reactant``, ``product``, or ``guess``).
    atoms : Sequence[str]
        Element symbols in atom order.
    coords : Sequence[Sequence[float]]
        Cartesian coordinates in Angstrom.
    charge : int
        Validated total charge.
    multiplicity : int
        Validated spin multiplicity.

    Returns
    -------
    str
        Title line, charge/multiplicity line, then one coordinate line
        per atom.

    Raises
    ------
    ValueError
        Raised as ``native_input_error`` when symbols are unknown, the
        slot is empty, counts disagree, or a coordinate is invalid.
    """
    if isinstance(atoms, (str, bytes)) or not isinstance(atoms, Sequence):
        raise ValueError(
            f"native_input_error: Gaussian QST {title!r} atoms must be "
            "a sequence of element symbols"
        )
    if isinstance(coords, (str, bytes)) or not isinstance(coords, Sequence):
        raise ValueError(
            f"native_input_error: Gaussian QST {title!r} coordinates must be "
            "a sequence of (x, y, z) triples"
        )
    symbols: list[str] = []
    for position, symbol in enumerate(atoms, start=1):
        try:
            symbols.append(canonical_element_symbol(symbol))
        except (ElementSymbolError, TypeError, ValueError) as exc:
            raise ValueError(
                f"native_input_error: Gaussian QST {title!r} atoms position "
                f"{position} is not a known element symbol: {symbol!r}"
            ) from exc
    if not symbols:
        raise ValueError(f"native_input_error: Gaussian QST {title!r} slot must not be empty")
    points = list(coords)
    if len(points) != len(symbols):
        raise ValueError(
            f"native_input_error: Gaussian QST {title!r} slot has "
            f"{len(symbols)} atoms but {len(points)} coordinate triples"
        )
    lines = [title, f"{charge} {multiplicity}"]
    for position, (symbol, point) in enumerate(zip(symbols, points), start=1):
        if isinstance(point, (str, bytes)):
            raise ValueError(
                "native_input_error: Gaussian QST "
                f"{title!r} coordinates for atom {position} must have exactly 3 values"
            )
        try:
            triple = tuple(point)
        except TypeError as exc:
            raise ValueError(
                "native_input_error: Gaussian QST "
                f"{title!r} coordinates for atom {position} must have exactly 3 values"
            ) from exc
        if len(triple) != 3:
            raise ValueError(
                "native_input_error: Gaussian QST "
                f"{title!r} coordinates for atom {position} must have exactly 3 values"
            )
        formatted = [_format_coordinate(value, title, position) for value in triple]
        lines.append(f"{symbol} {formatted[0]} {formatted[1]} {formatted[2]}")
    return "\n".join(lines)


def render_qst_molecule_specs(
    *,
    reactant_atoms: Sequence[str],
    reactant_coords: Sequence[Sequence[float]],
    product_atoms: Sequence[str],
    product_coords: Sequence[Sequence[float]],
    guess_atoms: Sequence[str] | None = None,
    guess_coords: Sequence[Sequence[float]] | None = None,
    charge: int | None,
    multiplicity: int | None,
) -> str:
    """Render the Gaussian QST2/QST3 molecule-specification section.

    Two slots render a QST2 section (reactant, product); adding both
    guess arguments renders a QST3 section (reactant, product, guess).
    Providing only one guess argument is rejected.

    Parameters
    ----------
    reactant_atoms : Sequence[str]
        Reactant element symbols in atom order.
    reactant_coords : Sequence[Sequence[float]]
        Reactant coordinates in Angstrom.
    product_atoms : Sequence[str]
        Product element symbols in atom order.
    product_coords : Sequence[Sequence[float]]
        Product coordinates in Angstrom.
    guess_atoms : Sequence[str] | None, optional
        Guess element symbols; ``None`` means a QST2 section.
    guess_coords : Sequence[Sequence[float]] | None, optional
        Guess coordinates; ``None`` means a QST2 section.
    charge : int | None
        Resolved total charge shared by every slot.
    multiplicity : int | None
        Resolved spin multiplicity shared by every slot.

    Returns
    -------
    str
        Spec blocks joined by single blank lines with a trailing
        newline; deterministic ``%.8f`` coordinates, no filenames.

    Raises
    ------
    ValueError
        Raised as ``native_input_error`` when charge/multiplicity are
        unresolved, guess arguments are half-provided, or any slot is
        malformed.
    """
    charge_value = _resolve_charge(charge)
    multiplicity_value = _resolve_multiplicity(multiplicity)
    if (guess_atoms is None) != (guess_coords is None):
        raise ValueError(
            "native_input_error: Gaussian QST guess needs both "
            "'guess_atoms' and 'guess_coords', or neither"
        )
    blocks = [
        _format_spec(
            REACTANT_TITLE,
            reactant_atoms,
            reactant_coords,
            charge_value,
            multiplicity_value,
        ),
        _format_spec(
            PRODUCT_TITLE, product_atoms, product_coords, charge_value, multiplicity_value
        ),
    ]
    if guess_atoms is not None and guess_coords is not None:
        blocks.append(
            _format_spec(GUESS_TITLE, guess_atoms, guess_coords, charge_value, multiplicity_value)
        )
    return "\n\n".join(blocks) + "\n"


def parse_qst_route(keyword: str, *, has_guess: bool) -> str:
    """Detect the QST flavor of a Gaussian route line and gate the slots.

    Detection is a case-insensitive whole-token search for ``QST2`` or
    ``QST3``; a bare ``QST`` without a flavor digit is not a supported
    route and is rejected. A route carrying both flavors is ambiguous
    and rejected.

    Parameters
    ----------
    keyword : str
        Full Gaussian route line, for example ``"B3LYP/6-31G* QST3 Opt"``.
    has_guess : bool
        Whether the caller provides a transition-state guess slot.
        ``QST2`` with a guess and ``QST3`` without one are rejected.

    Returns
    -------
    str
        ``"qst2"`` or ``"qst3"``.

    Raises
    ------
    ValueError
        Raised as ``native_input_error`` when *keyword* is not a
        non-empty string, carries no (or both) QST flavor(s),
        *has_guess* is not a boolean, or the flavor disagrees with the
        provided slots.
    """
    if not isinstance(keyword, str) or not keyword.strip():
        raise ValueError("native_input_error: Gaussian QST route must be a non-empty string")
    if not isinstance(has_guess, bool):
        raise ValueError(
            "native_input_error: Gaussian QST 'has_guess' must be a boolean, " f"got {has_guess!r}"
        )
    flavors = {f"qst{digit.lower()}" for digit in _QST_ITEM_PATTERN.findall(keyword)}
    if not flavors:
        raise ValueError(
            "native_input_error: Gaussian route carries no QST2/QST3 item; "
            f"got {keyword.strip()!r}"
        )
    if len(flavors) > 1:
        raise ValueError(
            "native_input_error: Gaussian route carries both QST2 and QST3; "
            "declare exactly one QST flavor"
        )
    flavor = next(iter(flavors))
    if flavor == "qst2" and has_guess:
        raise ValueError(
            "native_input_error: Gaussian QST2 takes exactly two molecule "
            "specifications; a guess slot was provided"
        )
    if flavor == "qst3" and not has_guess:
        raise ValueError(
            "native_input_error: Gaussian QST3 requires a transition-state "
            "guess slot, but none was provided"
        )
    return flavor
