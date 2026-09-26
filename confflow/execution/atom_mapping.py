#!/usr/bin/env python3

"""V4 atom mapping between named-structure slots (V4-5).

A QST/NEB work item combines several structures (reactant, product, optional
guess) whose atom orderings may differ.  This module fixes how those orderings
are related:

- ``identity``: every slot already lists the same elements in the same order;
- ``explicit_permutation``: slot ``i`` in reference order holds the atom that
  slot order ``permutation[i]`` holds, i.e. ``reference[i] == slot[perm[i]]``.

The mapping rides in step native params under the ``"atom_mapping"`` key so
it enters the semantic digest: different mappings digest differently, while
equivalent list/tuple spellings canonicalize to the same payload.

A permutation is never guessed or inferred: order disagreement under
``identity`` fails with ``atom_mapping_required``, and a malformed or
non-preserving explicit permutation fails with ``atom_mapping_invalid``.

Dependency rule: ``confflow.domain`` plus ``confflow.execution`` plus stdlib.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from ..domain.errors import DomainError
from .execution_adapters import REACTANT_SLOT

__all__ = [
    "ATOM_MAPPING_INVALID",
    "ATOM_MAPPING_NATIVE_KEY",
    "ATOM_MAPPING_REQUIRED",
    "AtomMapping",
    "AtomMappingError",
    "EXPLICIT_PERMUTATION_KIND",
    "IDENTITY_KIND",
    "apply_atom_permutation",
    "atom_mapping_digest_payload",
    "parse_atom_mapping",
    "reorder_slot_to_reference",
    "validate_atom_mapping",
]

#: The slots disagree in a way that needs an explicit mapping to proceed.
ATOM_MAPPING_REQUIRED: Final[str] = "atom_mapping_required"

#: An explicit mapping is malformed or does not preserve elements.
ATOM_MAPPING_INVALID: Final[str] = "atom_mapping_invalid"

#: Native-params key carrying the mapping into the semantic digest.
ATOM_MAPPING_NATIVE_KEY: Final[str] = "atom_mapping"

#: Mapping kind for slots that already share one atom ordering.
IDENTITY_KIND: Final[str] = "identity"

#: Mapping kind for slots related by an explicit index permutation.
EXPLICIT_PERMUTATION_KIND: Final[str] = "explicit_permutation"


class AtomMappingError(DomainError):
    """Two named slots cannot be related by an atom mapping.

    Parameters
    ----------
    message : str
        Human-readable failure description.
    code : str
        Machine-readable failure code (``atom_mapping_required`` when an
        explicit mapping is needed, ``atom_mapping_invalid`` when the
        supplied mapping is malformed or non-preserving).

    Attributes
    ----------
    code : str
        Machine-readable failure code carried for executor mapping.
    """

    def __init__(self, message: str, *, code: str) -> None:
        """Store *message* and the machine-readable *code*."""
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class AtomMapping:
    """How named-structure slots relate atom-by-atom.

    Parameters
    ----------
    kind : str
        ``"identity"`` or ``"explicit_permutation"``.
    permutation : tuple[int, ...]
        For ``"explicit_permutation"``, ``permutation[i]`` is the index in
        each non-reference slot holding reference atom ``i``; empty for
        ``"identity"``.  List spellings canonicalize to tuples.
    reference_slot : str
        Slot whose atom order is the reference (default ``"reactant"``).

    Raises
    ------
    AtomMappingError
        Raised when the kind is unknown, the permutation holds non-integer
        entries, or the reference slot is not a non-empty string.
    """

    kind: str
    permutation: tuple[int, ...] = ()
    reference_slot: str = REACTANT_SLOT

    def __post_init__(self) -> None:
        if self.kind not in (IDENTITY_KIND, EXPLICIT_PERMUTATION_KIND):
            raise AtomMappingError(
                f"unknown atom mapping kind {self.kind!r}",
                code=ATOM_MAPPING_INVALID,
            )
        permutation = self.permutation
        if isinstance(permutation, list):
            permutation = tuple(permutation)
            object.__setattr__(self, "permutation", permutation)
        if not isinstance(permutation, tuple) or any(
            isinstance(entry, bool) or not isinstance(entry, int) for entry in permutation
        ):
            raise AtomMappingError(
                "atom mapping permutation must be a sequence of integers",
                code=ATOM_MAPPING_INVALID,
            )
        if not isinstance(self.reference_slot, str) or not self.reference_slot:
            raise AtomMappingError(
                "atom mapping reference_slot must be a non-empty string",
                code=ATOM_MAPPING_INVALID,
            )


def parse_atom_mapping(value: Any) -> AtomMapping:
    """Parse native ``"atom_mapping"`` params into an :class:`AtomMapping`.

    Parameters
    ----------
    value : Any
        ``None`` (absent mapping), or a mapping with a ``"kind"`` entry:
        ``{"kind": "identity"}`` or ``{"kind": "explicit_permutation",
        "permutation": [...], "reference_slot": ...}``.  List and tuple
        permutation spellings canonicalize to the same object.

    Returns
    -------
    AtomMapping
        Canonical mapping; ``None`` parses to ``identity``.

    Raises
    ------
    AtomMappingError
        ``atom_mapping_invalid`` for unknown kinds, non-integer permutation
        entries, or a malformed reference slot.
    """
    if value is None:
        return AtomMapping(kind=IDENTITY_KIND)
    if not isinstance(value, Mapping):
        raise AtomMappingError(
            f"atom mapping must be a mapping or None, got {type(value).__name__}",
            code=ATOM_MAPPING_INVALID,
        )
    kind = value.get("kind")
    if kind == IDENTITY_KIND:
        return AtomMapping(kind=IDENTITY_KIND)
    if kind == EXPLICIT_PERMUTATION_KIND:
        permutation = value.get("permutation", ())
        if isinstance(permutation, (list, tuple)):
            entries: tuple[Any, ...] = tuple(permutation)
        else:
            raise AtomMappingError(
                "explicit atom mapping permutation must be a list of integers",
                code=ATOM_MAPPING_INVALID,
            )
        if any(isinstance(entry, bool) or not isinstance(entry, int) for entry in entries):
            raise AtomMappingError(
                "explicit atom mapping permutation must be a list of integers",
                code=ATOM_MAPPING_INVALID,
            )
        reference = value.get("reference_slot", REACTANT_SLOT)
        if not isinstance(reference, str) or not reference:
            raise AtomMappingError(
                "explicit atom mapping reference_slot must be a non-empty string",
                code=ATOM_MAPPING_INVALID,
            )
        return AtomMapping(
            kind=EXPLICIT_PERMUTATION_KIND,
            permutation=tuple(int(entry) for entry in entries),
            reference_slot=reference,
        )
    raise AtomMappingError(
        f"unknown atom mapping kind {kind!r}",
        code=ATOM_MAPPING_INVALID,
    )


def validate_atom_mapping(mapping: AtomMapping, slot_atoms: dict[str, tuple[str, ...]]) -> None:
    """Validate *mapping* against per-slot element sequences.

    Parameters
    ----------
    mapping : AtomMapping
        Parsed mapping under test.
    slot_atoms : dict[str, tuple[str, ...]]
        Element symbols per named slot.

    Returns
    -------
    None
        Success carries no value; every failure raises.

    Raises
    ------
    AtomMappingError
        ``atom_mapping_required`` when slots hold different atom counts or
        when ``identity`` faces order disagreement (an explicit mapping is
        needed but none was given).  ``atom_mapping_invalid`` when an
        explicit permutation is the wrong length, non-bijective,
        out-of-range, or not element-preserving.  A permutation is never
        inferred here.
    """
    if not slot_atoms:
        raise AtomMappingError(
            "atom mapping needs at least one named slot",
            code=ATOM_MAPPING_REQUIRED,
        )
    counts = {len(atoms) for atoms in slot_atoms.values()}
    if len(counts) != 1:
        raise AtomMappingError(
            "named slots hold different atom counts; an explicit mapping cannot fix that",
            code=ATOM_MAPPING_REQUIRED,
        )
    count = next(iter(counts))
    if count == 0:
        raise AtomMappingError(
            "named slots hold no atoms",
            code=ATOM_MAPPING_REQUIRED,
        )
    if mapping.kind == IDENTITY_KIND:
        anchor = slot_atoms.get(mapping.reference_slot)
        if anchor is None:
            anchor = slot_atoms[sorted(slot_atoms)[0]]
        for slot in sorted(slot_atoms):
            if tuple(slot_atoms[slot]) != tuple(anchor):
                raise AtomMappingError(
                    f"slot {slot!r} atom order differs from the reference; "
                    "an explicit permutation is required",
                    code=ATOM_MAPPING_REQUIRED,
                )
        return
    if mapping.kind != EXPLICIT_PERMUTATION_KIND:
        raise AtomMappingError(
            f"unknown atom mapping kind {mapping.kind!r}",
            code=ATOM_MAPPING_INVALID,
        )
    if mapping.reference_slot not in slot_atoms:
        raise AtomMappingError(
            f"reference slot {mapping.reference_slot!r} has no atoms",
            code=ATOM_MAPPING_INVALID,
        )
    permutation = tuple(mapping.permutation)
    if (
        len(permutation) != count
        or any(isinstance(entry, bool) or not isinstance(entry, int) for entry in permutation)
        or set(permutation) != set(range(count))
    ):
        raise AtomMappingError(
            "explicit atom mapping must be a bijective permutation of slot indices",
            code=ATOM_MAPPING_INVALID,
        )
    reference = tuple(slot_atoms[mapping.reference_slot])
    for slot in sorted(slot_atoms):
        if slot == mapping.reference_slot:
            continue
        atoms = tuple(slot_atoms[slot])
        for index in range(count):
            if reference[index] != atoms[permutation[index]]:
                raise AtomMappingError(
                    f"explicit atom mapping does not preserve elements for slot {slot!r}",
                    code=ATOM_MAPPING_INVALID,
                )


def apply_atom_permutation(
    atoms: tuple[str, ...] | list[str],
    coords: tuple[tuple[float, float, float], ...] | list[tuple[float, float, float]],
    permutation: tuple[int, ...] | list[int],
) -> tuple[tuple[str, ...], tuple[tuple[float, float, float], ...]]:
    """Reorder one slot into reference order.

    Parameters
    ----------
    atoms : tuple[str, ...] | list[str]
        Element symbols in slot order.
    coords : tuple | list
        Coordinates in slot order, parallel to *atoms*.
    permutation : tuple[int, ...] | list[int]
        ``permutation[i]`` selects the slot index holding reference atom
        ``i``; the identity permutation returns the inputs unchanged.

    Returns
    -------
    tuple
        ``(atoms, coords)`` in reference order as fresh tuples.

    Raises
    ------
    AtomMappingError
        ``atom_mapping_invalid`` when the permutation length disagrees
        with the slot or holds non-integer/out-of-range entries.
    """
    atom_items = tuple(atoms)
    coord_items = tuple(coords)
    perm = tuple(permutation)
    if len(atom_items) != len(coord_items):
        raise AtomMappingError(
            "atoms and coordinates must be parallel",
            code=ATOM_MAPPING_INVALID,
        )
    if len(perm) != len(atom_items) or any(
        isinstance(entry, bool) or not isinstance(entry, int) for entry in perm
    ):
        raise AtomMappingError(
            "permutation length must match the slot atom count",
            code=ATOM_MAPPING_INVALID,
        )
    if set(perm) != set(range(len(atom_items))):
        raise AtomMappingError(
            "permutation must be a bijective reordering of slot indices",
            code=ATOM_MAPPING_INVALID,
        )
    if perm == tuple(range(len(atom_items))):
        return atom_items, coord_items
    return tuple(atom_items[index] for index in perm), tuple(coord_items[index] for index in perm)


def atom_mapping_digest_payload(mapping: AtomMapping) -> dict[str, Any]:
    """Return the canonical digest payload of *mapping*.

    Parameters
    ----------
    mapping : AtomMapping
        Mapping whose digest identity is needed.

    Returns
    -------
    dict
        ``{"kind", "permutation": list, "reference_slot"}``; equivalent
        list/tuple spellings yield equal payloads while different mappings
        yield different payloads.
    """
    return {
        "kind": mapping.kind,
        "permutation": list(mapping.permutation),
        "reference_slot": mapping.reference_slot,
    }


def reorder_slot_to_reference(
    *,
    reference_atoms: tuple[str, ...] | list[str],
    slot_atoms: tuple[str, ...] | list[str],
    slot_coords: tuple[tuple[float, float, float], ...] | list[tuple[float, float, float]],
    mapping: AtomMapping,
) -> tuple[tuple[str, ...], tuple[tuple[float, float, float], ...]]:
    """Return the execution-local slot representation in reference order.

    Parameters
    ----------
    reference_atoms : tuple[str, ...] | list[str]
        Element symbols of the reference slot (length gate only).
    slot_atoms : tuple[str, ...] | list[str]
        Element symbols of the slot to reorder.
    slot_coords : tuple | list
        Coordinates of the slot to reorder, parallel to *slot_atoms*.
    mapping : AtomMapping
        Identity returns tuple copies; an explicit permutation reorders.

    Returns
    -------
    tuple
        ``(atoms, coords)`` in reference order; input records are never
        mutated (fresh tuples are always returned).

    Raises
    ------
    AtomMappingError
        ``atom_mapping_required`` when the slot length disagrees with the
        reference; ``atom_mapping_invalid`` for a malformed permutation.
    """
    reference_items = tuple(reference_atoms)
    atom_items = tuple(slot_atoms)
    coord_items = tuple(slot_coords)
    if len(atom_items) != len(reference_items) or len(atom_items) != len(coord_items):
        raise AtomMappingError(
            "slot length disagrees with the reference slot",
            code=ATOM_MAPPING_REQUIRED,
        )
    if mapping.kind == IDENTITY_KIND:
        return atom_items, coord_items
    if mapping.kind != EXPLICIT_PERMUTATION_KIND:
        raise AtomMappingError(
            f"unknown atom mapping kind {mapping.kind!r}",
            code=ATOM_MAPPING_INVALID,
        )
    return apply_atom_permutation(atom_items, coord_items, tuple(mapping.permutation))
