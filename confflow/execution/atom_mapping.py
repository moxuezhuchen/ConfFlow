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

from ..domain._immutable import FrozenDict
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
    "normalize_to_per_slot",
    "parse_atom_mapping",
    "reorder_slot_to_reference",
    "validate_atom_mapping",
    "validate_mapping_for_slots",
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
        Uniform shorthand for ``"explicit_permutation"``: ``permutation[i]``
        is the index in *each* non-reference slot holding reference atom
        ``i``; empty for ``"identity"``.  List spellings canonicalize to
        tuples.  Mutually exclusive with *permutations*: supplying both is a
        second authority and fails.
    reference_slot : str
        Slot whose atom order is the reference (default ``"reactant"``).
    permutations : Mapping[str, tuple[int, ...]] | None
        Per-slot explicit mappings: ``permutations[slot][i]`` selects the
        index in *slot* holding reference atom ``i``.  Each slot is
        validated independently, so QST3 product and guess may use different
        reorderings.  ``None`` (or empty) means no per-slot mapping; a lone
        *permutation* normalizes to this form via
        :func:`normalize_to_per_slot`.

    Raises
    ------
    AtomMappingError
        Raised when the kind is unknown, the permutation holds non-integer
        entries, the reference slot is not a non-empty string, or both
        permutation authorities are supplied.
    """

    kind: str
    permutation: tuple[int, ...] = ()
    reference_slot: str = REACTANT_SLOT
    permutations: Any = None

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
        raw = self.permutations
        if raw is None:
            object.__setattr__(self, "permutations", FrozenDict())
            raw = object.__getattribute__(self, "permutations")
        else:
            if isinstance(raw, FrozenDict):
                items = dict(raw)
            elif isinstance(raw, Mapping):
                items = dict(raw)
            else:
                raise AtomMappingError(
                    "atom mapping permutations must be a mapping of slot to index lists",
                    code=ATOM_MAPPING_INVALID,
                )
            normalized: dict[str, tuple[int, ...]] = {}
            for slot, entries in items.items():
                if not isinstance(slot, str) or not slot:
                    raise AtomMappingError(
                        "atom mapping permutations keys must be non-empty strings",
                        code=ATOM_MAPPING_INVALID,
                    )
                if isinstance(entries, list):
                    entries = tuple(entries)
                if not isinstance(entries, tuple) or any(
                    isinstance(e, bool) or not isinstance(e, int) for e in entries
                ):
                    raise AtomMappingError(
                        f"atom mapping permutations[{slot!r}] must be a sequence of integers",
                        code=ATOM_MAPPING_INVALID,
                    )
                normalized[slot] = tuple(int(e) for e in entries)
            object.__setattr__(self, "permutations", FrozenDict(normalized))
        per_slot = object.__getattribute__(self, "permutations")
        if len(per_slot):
            if self.kind != EXPLICIT_PERMUTATION_KIND:
                raise AtomMappingError(
                    "per-slot permutations require kind 'explicit_permutation'",
                    code=ATOM_MAPPING_INVALID,
                )
            if tuple(self.permutation):
                raise AtomMappingError(
                    "atom mapping carries two authorities: supply either "
                    "'permutation' (uniform shorthand) or 'permutations' "
                    "(per-slot), never both",
                    code=ATOM_MAPPING_INVALID,
                )
            if self.reference_slot in per_slot:
                raise AtomMappingError(
                    "atom mapping permutations must not carry the reference slot",
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
        if "permutation" in value or "permutations" in value:
            raise AtomMappingError(
                "identity mapping must not carry permutation entries",
                code=ATOM_MAPPING_INVALID,
            )
        return AtomMapping(kind=IDENTITY_KIND)
    if kind == EXPLICIT_PERMUTATION_KIND:
        has_uniform = "permutation" in value
        has_per_slot = "permutations" in value
        if has_uniform and has_per_slot:
            raise AtomMappingError(
                "atom mapping carries two authorities: supply either "
                "'permutation' or 'permutations', never both",
                code=ATOM_MAPPING_INVALID,
            )
        reference = value.get("reference_slot", REACTANT_SLOT)
        if not isinstance(reference, str) or not reference:
            raise AtomMappingError(
                "explicit atom mapping reference_slot must be a non-empty string",
                code=ATOM_MAPPING_INVALID,
            )
        if has_per_slot:
            raw_slots = value.get("permutations")
            if not isinstance(raw_slots, Mapping):
                raise AtomMappingError(
                    "explicit atom mapping permutations must be a mapping of slot to lists",
                    code=ATOM_MAPPING_INVALID,
                )
            normalized: dict[str, tuple[int, ...]] = {}
            for slot, entries in raw_slots.items():
                if not isinstance(slot, str) or not slot:
                    raise AtomMappingError(
                        "explicit atom mapping permutations keys must be non-empty strings",
                        code=ATOM_MAPPING_INVALID,
                    )
                if not isinstance(entries, (list, tuple)):
                    raise AtomMappingError(
                        f"explicit atom mapping permutations[{slot!r}] must be a list of integers",
                        code=ATOM_MAPPING_INVALID,
                    )
                if any(isinstance(e, bool) or not isinstance(e, int) for e in entries):
                    raise AtomMappingError(
                        f"explicit atom mapping permutations[{slot!r}] must be a list of integers",
                        code=ATOM_MAPPING_INVALID,
                    )
                normalized[slot] = tuple(int(e) for e in entries)
            return AtomMapping(
                kind=EXPLICIT_PERMUTATION_KIND,
                reference_slot=reference,
                permutations=FrozenDict(normalized),
            )
        permutation = value.get("permutation", ())
        if isinstance(permutation, (list, tuple)):
            uniform_entries: tuple[Any, ...] = tuple(permutation)
        else:
            raise AtomMappingError(
                "explicit atom mapping permutation must be a list of integers",
                code=ATOM_MAPPING_INVALID,
            )
        if any(isinstance(entry, bool) or not isinstance(entry, int) for entry in uniform_entries):
            raise AtomMappingError(
                "explicit atom mapping permutation must be a list of integers",
                code=ATOM_MAPPING_INVALID,
            )
        return AtomMapping(
            kind=EXPLICIT_PERMUTATION_KIND,
            permutation=tuple(int(entry) for entry in uniform_entries),
            reference_slot=reference,
        )
    raise AtomMappingError(
        f"unknown atom mapping kind {kind!r}",
        code=ATOM_MAPPING_INVALID,
    )


def normalize_to_per_slot(
    mapping: AtomMapping,
    slots: tuple[str, ...] | list[str],
) -> dict[str, tuple[int, ...]]:
    """Normalize *mapping* once to per-slot form.

    Identity normalizes to ``{}`` (no reordering needed).  A uniform
    *permutation* expands to every non-reference slot in *slots*; an
    explicit *permutations* mapping is returned as-is (sorted validation of
    slot membership is left to :func:`validate_mapping_for_slots`).  The
    uniform spelling is a shorthand, never a second authority: after this
    call every consumer works from the per-slot dict only.
    """
    ordered = tuple(slots)
    if mapping.kind == IDENTITY_KIND:
        return {}
    per_slot = dict(mapping.permutations) if len(mapping.permutations) else {}
    if per_slot:
        return {slot: tuple(per_slot[slot]) for slot in sorted(per_slot)}
    uniform = tuple(mapping.permutation)
    return {slot: uniform for slot in ordered if slot != mapping.reference_slot}


def _check_bijective_permutation(
    permutation: tuple[int, ...],
    count: int,
    *,
    slot: str,
) -> None:
    if (
        len(permutation) != count
        or any(isinstance(e, bool) or not isinstance(e, int) for e in permutation)
        or set(permutation) != set(range(count))
    ):
        raise AtomMappingError(
            f"explicit atom mapping for slot {slot!r} must be a bijective "
            "permutation of slot indices",
            code=ATOM_MAPPING_INVALID,
        )


def validate_mapping_for_slots(
    mapping: AtomMapping, slot_atoms: dict[str, tuple[str, ...]]
) -> dict[str, tuple[int, ...]]:
    """Validate *mapping* against per-slot elements and return per-slot perms.

    This is the single shared helper both assembly and the executor use
    *before* any element-compatibility check: mapping is applied first,
    compatibility second.  Identity requires equal counts and exactly equal
    element order.  Explicit mappings (uniform shorthand or per-slot) must be
    bijective and element-preserving per slot.  Returns the normalized
    per-slot dict (``{}`` for identity).
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
        if len(mapping.permutations):
            raise AtomMappingError(
                "identity mapping must not carry per-slot permutations",
                code=ATOM_MAPPING_INVALID,
            )
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
        return {}
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
    per_slot = normalize_to_per_slot(mapping, tuple(sorted(slot_atoms)))
    # Every non-reference slot must have exactly one permutation.
    expected_slots = sorted(s for s in slot_atoms if s != mapping.reference_slot)
    if set(per_slot) != set(expected_slots):
        raise AtomMappingError(
            "explicit atom mapping must supply one permutation per non-reference slot",
            code=ATOM_MAPPING_INVALID,
        )
    reference = tuple(slot_atoms[mapping.reference_slot])
    for slot in sorted(per_slot):
        perm = tuple(per_slot[slot])
        _check_bijective_permutation(perm, count, slot=slot)
        atoms = tuple(slot_atoms[slot])
        for index in range(count):
            if reference[index] != atoms[perm[index]]:
                raise AtomMappingError(
                    f"explicit atom mapping does not preserve elements for slot {slot!r}",
                    code=ATOM_MAPPING_INVALID,
                )
    return per_slot


def validate_atom_mapping(mapping: AtomMapping, slot_atoms: dict[str, tuple[str, ...]]) -> None:
    """Validate *mapping* against per-slot element sequences.

    Backward-compatible uniform entry point: delegates to
    :func:`validate_mapping_for_slots`, which additionally supports per-slot
    ``permutations``.  Uniform semantics are unchanged.
    """
    validate_mapping_for_slots(mapping, slot_atoms)
    return None


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

    Legacy uniform spellings keep the canonical ``{"kind", "permutation",
    "reference_slot"}`` shape; per-slot mappings add a sorted
    ``"permutations"`` member so independent product/guess reorderings digest
    differently while equivalent list/tuple spellings stay equal.
    """
    payload: dict[str, Any] = {
        "kind": mapping.kind,
        "permutation": list(mapping.permutation),
        "reference_slot": mapping.reference_slot,
    }
    per_slot = dict(mapping.permutations) if len(mapping.permutations) else {}
    if per_slot:
        payload["permutations"] = {slot: list(per_slot[slot]) for slot in sorted(per_slot)}
    return payload


def reorder_slot_to_reference(
    *,
    reference_atoms: tuple[str, ...] | list[str],
    slot_atoms: tuple[str, ...] | list[str],
    slot_coords: tuple[tuple[float, float, float], ...] | list[tuple[float, float, float]],
    mapping: AtomMapping,
    slot: str | None = None,
) -> tuple[tuple[str, ...], tuple[tuple[float, float, float], ...]]:
    """Return the execution-local slot representation in reference order.

    Identity returns tuple copies.  A uniform explicit permutation reorders
    with ``mapping.permutation``; a per-slot mapping reorders with
    ``mapping.permutations[slot]`` (callers pass the slot being reordered).
    Input records are never mutated.
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
    per_slot = dict(mapping.permutations) if len(mapping.permutations) else {}
    if per_slot:
        if slot is None or slot not in per_slot:
            raise AtomMappingError(
                "per-slot atom mapping requires the slot name being reordered",
                code=ATOM_MAPPING_INVALID,
            )
        return apply_atom_permutation(atom_items, coord_items, tuple(per_slot[slot]))
    return apply_atom_permutation(atom_items, coord_items, tuple(mapping.permutation))
