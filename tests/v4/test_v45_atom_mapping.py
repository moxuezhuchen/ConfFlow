#!/usr/bin/env python3

"""V4-5 atom mapping tests.

Covers :mod:`confflow.execution.atom_mapping` end to end:

- parsing (absent/identity/explicit spellings, canonicalization, rejections);
- validation (identity agreement, explicit bijective element preservation);
- digest payload stability (list vs tuple spellings, distinct mappings);
- deterministic reordering with immutable inputs.

Every failure is asserted as a typed :class:`AtomMappingError` with its
machine-readable code; the module under test has no renderer, so typed
error assertions alone prove failures precede any rendering.
"""

from __future__ import annotations

from typing import Any

import pytest

from confflow.domain.canonical import typed_digest
from confflow.domain.errors import DomainError
from confflow.execution.atom_mapping import (
    AtomMapping,
    AtomMappingError,
    apply_atom_permutation,
    atom_mapping_digest_payload,
    parse_atom_mapping,
    reorder_slot_to_reference,
    validate_atom_mapping,
)

REFERENCE_ATOMS = ("O", "H", "C")
REFERENCE_COORDS = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0))

#: ``reference[i] == slot[perm[i]]`` with ``perm == (2, 0, 1)``.
PERMUTATION = (2, 0, 1)
PERMUTED_ATOMS = ("H", "C", "O")
PERMUTED_COORDS = ((1.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 0.0, 0.0))

DIGEST_KIND = "test.atom_mapping.v1"


class TestParse:
    """Native mapping params parse into canonical mappings."""

    def test_none_is_identity(self) -> None:
        assert parse_atom_mapping(None) == AtomMapping(kind="identity")

    def test_identity_spelling(self) -> None:
        assert parse_atom_mapping({"kind": "identity"}) == AtomMapping(kind="identity")

    def test_explicit_list_spelling(self) -> None:
        mapping = parse_atom_mapping({"kind": "explicit_permutation", "permutation": [2, 0, 1]})
        assert mapping == AtomMapping(kind="explicit_permutation", permutation=(2, 0, 1))

    def test_explicit_tuple_spelling_canonicalizes(self) -> None:
        from_list = parse_atom_mapping({"kind": "explicit_permutation", "permutation": [2, 0, 1]})
        from_tuple = parse_atom_mapping({"kind": "explicit_permutation", "permutation": (2, 0, 1)})
        assert from_list == from_tuple
        assert from_tuple.permutation == (2, 0, 1)

    def test_explicit_reference_slot_defaults_to_reactant(self) -> None:
        mapping = parse_atom_mapping({"kind": "explicit_permutation", "permutation": [0]})
        assert mapping.reference_slot == "reactant"

    def test_explicit_reference_slot_passes_through(self) -> None:
        mapping = parse_atom_mapping(
            {
                "kind": "explicit_permutation",
                "permutation": [0],
                "reference_slot": "product",
            }
        )
        assert mapping.reference_slot == "product"

    def test_errors_are_domain_errors(self) -> None:
        assert issubclass(AtomMappingError, DomainError)

    @pytest.mark.parametrize("value", [{"kind": "fuzzy"}, {"kind": "IDENTITY"}, {}, {"kind": None}])
    def test_unknown_kind_rejected(self, value: dict[str, Any]) -> None:
        with pytest.raises(AtomMappingError) as exc:
            parse_atom_mapping(value)
        assert exc.value.code == "atom_mapping_invalid"

    @pytest.mark.parametrize("value", ["identity", [1], 5, True])
    def test_non_mapping_rejected(self, value: Any) -> None:
        with pytest.raises(AtomMappingError) as exc:
            parse_atom_mapping(value)
        assert exc.value.code == "atom_mapping_invalid"

    @pytest.mark.parametrize(
        "permutation",
        [[0.0, 1], ["a"], [None], [True], "012", 7, {"0": 0}],
    )
    def test_non_integer_permutation_rejected(self, permutation: Any) -> None:
        with pytest.raises(AtomMappingError) as exc:
            parse_atom_mapping({"kind": "explicit_permutation", "permutation": permutation})
        assert exc.value.code == "atom_mapping_invalid"

    @pytest.mark.parametrize("reference", ["", None, 5])
    def test_bad_reference_slot_rejected(self, reference: Any) -> None:
        with pytest.raises(AtomMappingError) as exc:
            parse_atom_mapping(
                {
                    "kind": "explicit_permutation",
                    "permutation": [0],
                    "reference_slot": reference,
                }
            )
        assert exc.value.code == "atom_mapping_invalid"


class TestValidateIdentity:
    """Identity demands equal counts and exactly equal element order."""

    def test_identity_ok_two_slots(self) -> None:
        validate_atom_mapping(
            AtomMapping(kind="identity"),
            {"reactant": REFERENCE_ATOMS, "product": REFERENCE_ATOMS},
        )

    def test_identity_ok_three_slots(self) -> None:
        validate_atom_mapping(
            AtomMapping(kind="identity"),
            {
                "reactant": REFERENCE_ATOMS,
                "product": REFERENCE_ATOMS,
                "guess": REFERENCE_ATOMS,
            },
        )

    def test_identity_wrong_order_needs_explicit(self) -> None:
        with pytest.raises(AtomMappingError) as exc:
            validate_atom_mapping(
                AtomMapping(kind="identity"),
                {"reactant": REFERENCE_ATOMS, "product": PERMUTED_ATOMS},
            )
        assert exc.value.code == "atom_mapping_required"

    def test_identity_count_mismatch_needs_mapping(self) -> None:
        with pytest.raises(AtomMappingError) as exc:
            validate_atom_mapping(
                AtomMapping(kind="identity"),
                {"reactant": REFERENCE_ATOMS, "product": ("O", "H")},
            )
        assert exc.value.code == "atom_mapping_required"

    def test_explicit_count_mismatch_needs_mapping(self) -> None:
        mapping = AtomMapping(kind="explicit_permutation", permutation=PERMUTATION)
        with pytest.raises(AtomMappingError) as exc:
            validate_atom_mapping(
                mapping,
                {"reactant": REFERENCE_ATOMS, "product": ("O", "H")},
            )
        assert exc.value.code == "atom_mapping_required"


class TestValidateExplicit:
    """Explicit permutations must be bijective and element-preserving."""

    def test_explicit_ok(self) -> None:
        validate_atom_mapping(
            AtomMapping(kind="explicit_permutation", permutation=PERMUTATION),
            {"reactant": REFERENCE_ATOMS, "product": PERMUTED_ATOMS},
        )

    def test_explicit_ok_three_slots(self) -> None:
        validate_atom_mapping(
            AtomMapping(kind="explicit_permutation", permutation=PERMUTATION),
            {
                "reactant": REFERENCE_ATOMS,
                "product": PERMUTED_ATOMS,
                "guess": PERMUTED_ATOMS,
            },
        )

    def test_explicit_wrong_length_invalid(self) -> None:
        mapping = AtomMapping(kind="explicit_permutation", permutation=(1, 0))
        with pytest.raises(AtomMappingError) as exc:
            validate_atom_mapping(
                mapping,
                {"reactant": REFERENCE_ATOMS, "product": PERMUTED_ATOMS},
            )
        assert exc.value.code == "atom_mapping_invalid"

    def test_explicit_duplicate_index_invalid(self) -> None:
        mapping = AtomMapping(kind="explicit_permutation", permutation=(0, 0, 1))
        with pytest.raises(AtomMappingError) as exc:
            validate_atom_mapping(
                mapping,
                {"reactant": ("O", "O", "O"), "product": ("O", "O", "O")},
            )
        assert exc.value.code == "atom_mapping_invalid"

    def test_explicit_missing_index_invalid(self) -> None:
        mapping = AtomMapping(kind="explicit_permutation", permutation=(0, 1, 1))
        with pytest.raises(AtomMappingError) as exc:
            validate_atom_mapping(
                mapping,
                {"reactant": ("O", "O", "O"), "product": ("O", "O", "O")},
            )
        assert exc.value.code == "atom_mapping_invalid"

    def test_explicit_out_of_range_invalid(self) -> None:
        mapping = AtomMapping(kind="explicit_permutation", permutation=(0, 1, 3))
        with pytest.raises(AtomMappingError) as exc:
            validate_atom_mapping(
                mapping,
                {"reactant": REFERENCE_ATOMS, "product": PERMUTED_ATOMS},
            )
        assert exc.value.code == "atom_mapping_invalid"

    def test_explicit_element_mismatch_invalid(self) -> None:
        mapping = AtomMapping(kind="explicit_permutation", permutation=PERMUTATION)
        with pytest.raises(AtomMappingError) as exc:
            validate_atom_mapping(
                mapping,
                {"reactant": REFERENCE_ATOMS, "product": ("H", "C", "N")},
            )
        assert exc.value.code == "atom_mapping_invalid"

    def test_explicit_bijective_but_wrong_order_invalid(self) -> None:
        mapping = AtomMapping(kind="explicit_permutation", permutation=(0, 1, 2))
        with pytest.raises(AtomMappingError) as exc:
            validate_atom_mapping(
                mapping,
                {"reactant": REFERENCE_ATOMS, "product": PERMUTED_ATOMS},
            )
        assert exc.value.code == "atom_mapping_invalid"

    def test_explicit_missing_reference_slot_invalid(self) -> None:
        mapping = AtomMapping(
            kind="explicit_permutation",
            permutation=PERMUTATION,
            reference_slot="reactant",
        )
        with pytest.raises(AtomMappingError) as exc:
            validate_atom_mapping(
                mapping,
                {"product": PERMUTED_ATOMS, "guess": PERMUTED_ATOMS},
            )
        assert exc.value.code == "atom_mapping_invalid"


class TestDigestPayload:
    """Equivalent spellings share a digest; different mappings diverge."""

    def test_list_vs_tuple_same_payload(self) -> None:
        from_list = parse_atom_mapping({"kind": "explicit_permutation", "permutation": [2, 0, 1]})
        from_tuple = parse_atom_mapping({"kind": "explicit_permutation", "permutation": (2, 0, 1)})
        assert atom_mapping_digest_payload(from_list) == atom_mapping_digest_payload(from_tuple)
        assert typed_digest(DIGEST_KIND, atom_mapping_digest_payload(from_list)) == typed_digest(
            DIGEST_KIND, atom_mapping_digest_payload(from_tuple)
        )

    def test_payload_shape_is_canonical(self) -> None:
        payload = atom_mapping_digest_payload(AtomMapping(kind="identity"))
        assert payload == {"kind": "identity", "permutation": [], "reference_slot": "reactant"}

    def test_different_permutation_different_digest(self) -> None:
        first = atom_mapping_digest_payload(
            AtomMapping(kind="explicit_permutation", permutation=(2, 0, 1))
        )
        second = atom_mapping_digest_payload(
            AtomMapping(kind="explicit_permutation", permutation=(0, 1, 2))
        )
        assert first != second
        assert typed_digest(DIGEST_KIND, first) != typed_digest(DIGEST_KIND, second)

    def test_different_kind_different_digest(self) -> None:
        identity = atom_mapping_digest_payload(AtomMapping(kind="identity"))
        explicit = atom_mapping_digest_payload(
            AtomMapping(kind="explicit_permutation", permutation=(0, 1, 2))
        )
        assert typed_digest(DIGEST_KIND, identity) != typed_digest(DIGEST_KIND, explicit)


class TestReorder:
    """Reordering is deterministic and never mutates its inputs."""

    def test_apply_explicit_reorders_atoms_and_coords(self) -> None:
        atoms, coords = apply_atom_permutation(PERMUTED_ATOMS, PERMUTED_COORDS, PERMUTATION)
        assert atoms == REFERENCE_ATOMS
        assert coords == REFERENCE_COORDS

    def test_apply_identity_returns_inputs_unchanged(self) -> None:
        atoms, coords = apply_atom_permutation(REFERENCE_ATOMS, REFERENCE_COORDS, (0, 1, 2))
        assert atoms == REFERENCE_ATOMS
        assert coords == REFERENCE_COORDS

    def test_apply_is_deterministic(self) -> None:
        first = apply_atom_permutation(PERMUTED_ATOMS, PERMUTED_COORDS, PERMUTATION)
        second = apply_atom_permutation(PERMUTED_ATOMS, PERMUTED_COORDS, PERMUTATION)
        assert first == second

    def test_apply_rejects_bad_permutation(self) -> None:
        with pytest.raises(AtomMappingError) as exc:
            apply_atom_permutation(REFERENCE_ATOMS, REFERENCE_COORDS, (0, 0, 1))
        assert exc.value.code == "atom_mapping_invalid"

    def test_reorder_slot_to_reference_explicit(self) -> None:
        mapping = AtomMapping(kind="explicit_permutation", permutation=PERMUTATION)
        atoms, coords = reorder_slot_to_reference(
            reference_atoms=REFERENCE_ATOMS,
            slot_atoms=PERMUTED_ATOMS,
            slot_coords=PERMUTED_COORDS,
            mapping=mapping,
        )
        assert atoms == REFERENCE_ATOMS
        assert coords == REFERENCE_COORDS

    def test_reorder_slot_to_reference_identity(self) -> None:
        atoms, coords = reorder_slot_to_reference(
            reference_atoms=REFERENCE_ATOMS,
            slot_atoms=REFERENCE_ATOMS,
            slot_coords=REFERENCE_COORDS,
            mapping=AtomMapping(kind="identity"),
        )
        assert atoms == REFERENCE_ATOMS
        assert coords == REFERENCE_COORDS

    def test_reorder_never_mutates_inputs(self) -> None:
        slot_atoms = ["H", "C", "O"]
        slot_coords = [[1.0, 0.0, 0.0], [2.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
        snapshot_atoms = list(slot_atoms)
        snapshot_coords = [list(point) for point in slot_coords]
        mapping = AtomMapping(kind="explicit_permutation", permutation=PERMUTATION)
        reorder_slot_to_reference(
            reference_atoms=list(REFERENCE_ATOMS),
            slot_atoms=slot_atoms,
            slot_coords=slot_coords,  # type: ignore[arg-type]
            mapping=mapping,
        )
        assert slot_atoms == snapshot_atoms
        assert slot_coords == snapshot_coords
