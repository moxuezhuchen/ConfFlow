#!/usr/bin/env python3

"""Structure identity tests for the V4 domain layer.

Pins construction invariants, the geometry content digest axis, and the
ordered-set semantics that downstream assembly relies on.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any

import pytest

from confflow.domain import (
    InvalidStructureError,
    StructureRecord,
    StructureSet,
)
from tests.v4._builders import structure

WATER_ATOMS = ("O", "H", "H")
WATER_COORDINATES = (
    (0.0, 0.0, 0.0),
    (0.76, 0.59, 0.0),
    (0.76, -0.59, 0.0),
)


def record(**overrides: Any) -> StructureRecord:
    """Build a minimal water record with overridable fields."""
    fields: dict[str, Any] = {
        "id": "s1",
        "atoms": WATER_ATOMS,
        "coordinates": WATER_COORDINATES,
    }
    fields.update(overrides)
    return StructureRecord(**fields)


@pytest.mark.parametrize(
    ("atoms", "coordinates", "match"),
    [
        pytest.param(
            ("O", "H"),
            ((0.0, 0.0, 0.0),),
            "must have the same length",
            id="atom-coordinate-count-mismatch",
        ),
        pytest.param(
            ("O",),
            ((0.0, 0.0),),
            "must have exactly 3 values",
            id="wrong-coordinate-arity",
        ),
        pytest.param(
            ("O",),
            ((math.nan, 0.0, 0.0),),
            "must be finite",
            id="nan-coordinate",
        ),
        pytest.param(
            ("O",),
            ((0.0, math.inf, 0.0),),
            "must be finite",
            id="infinite-coordinate",
        ),
        pytest.param(
            ("O",),
            ((True, 0.0, 0.0),),
            "must be a real number",
            id="bool-coordinate",
        ),
        pytest.param(
            ("C1", "H"),
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
            "unknown element symbol",
            id="atom-label-c1",
        ),
        pytest.param(
            ("O_chain",),
            ((0.0, 0.0, 0.0),),
            "unknown element symbol",
            id="atom-label-suffix",
        ),
        pytest.param((), (), "atoms must not be empty", id="empty-atoms"),
        pytest.param(
            ("O",),
            (),
            "coordinates must not be empty",
            id="empty-coordinates",
        ),
        pytest.param(
            "O",
            WATER_COORDINATES,
            "atoms must be a sequence",
            id="atoms-string",
        ),
        pytest.param(
            ("O",),
            "not-coordinates",
            "coordinates must be a sequence",
            id="coordinates-string",
        ),
    ],
)
def test_structure_rejects_invalid_geometry(atoms: Any, coordinates: Any, match: str) -> None:
    """Invalid atoms or coordinates fail with InvalidStructureError."""
    with pytest.raises(InvalidStructureError, match=match):
        StructureRecord(id="s1", atoms=atoms, coordinates=coordinates)


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        pytest.param({"id": ""}, "id must be a non-empty string", id="empty-id"),
        pytest.param({"id": " s1 "}, "surrounding whitespace", id="padded-id"),
        pytest.param({"multiplicity": 0}, "multiplicity must be >= 1", id="zero-multiplicity"),
        pytest.param({"multiplicity": True}, "integer or None", id="bool-multiplicity"),
        pytest.param({"charge": 1.5}, "charge must be an integer", id="float-charge"),
        pytest.param({"ordinal": -1}, "ordinal must be >= 0", id="negative-ordinal"),
        pytest.param(
            {"parent_ids": ("s1",)},
            "must not list itself as parent",
            id="self-parent",
        ),
        pytest.param(
            {"lineage_root_id": ""},
            "lineage_root_id must be a non-empty string",
            id="empty-lineage-root",
        ),
    ],
)
def test_structure_rejects_invalid_scalar_fields(overrides: dict[str, Any], match: str) -> None:
    """Out-of-range or self-referential scalar fields are rejected."""
    with pytest.raises(InvalidStructureError, match=match):
        record(**overrides)


def test_structure_canonicalizes_atom_symbols() -> None:
    """Atom symbols are canonicalized to the legacy capitalization."""
    result = record(atoms=("o", "CL"), coordinates=((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)))
    assert result.atoms == ("O", "Cl")


def test_structure_is_frozen_with_nested_tuple_coordinates() -> None:
    """Records are immutable, hashable, and fully tuple-frozen."""
    result = record(metadata={"note": ["a", "b"], "count": 2})
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.id = "other"  # type: ignore[misc]
    assert isinstance(result.coordinates, tuple)
    assert all(isinstance(point, tuple) for point in result.coordinates)
    assert isinstance(hash(result), int)
    assert hash(record()) == hash(record())


def test_geometry_digest_is_deterministic_across_rebuilds() -> None:
    """Rebuilding the same geometry yields the same digest."""
    assert structure("s1").geometry_digest == structure("s1").geometry_digest


def test_geometry_digest_ignores_identity_and_annotations() -> None:
    """Identity, provenance, charge, multiplicity, and metadata are excluded."""
    base = structure("s1")
    ordinal_variant = record(id="s2", ordinal=7)
    variants = [
        structure("s2"),
        ordinal_variant,
        structure("s1", metadata={"note": ["a", "b"], "count": 2}),
        structure("s1", charge=2),
        structure("s1", multiplicity=3),
        structure("s1", role="product"),
        structure("s1", group_key="g1"),
        structure("s1", parent_ids=("p1",)),
        structure("s1", lineage_root_id="root"),
    ]
    for variant in variants:
        assert variant.geometry_digest == base.geometry_digest


@pytest.mark.parametrize(
    "variant",
    [
        pytest.param(
            record(
                atoms=("H", "O", "H"),
                coordinates=(WATER_COORDINATES[1], WATER_COORDINATES[0], WATER_COORDINATES[2]),
            ),
            id="atom-order-permutation",
        ),
        pytest.param(structure("s1", offset=0.25), id="coordinate-shift"),
        pytest.param(record(atoms=("S", "H", "H")), id="atom-substitution"),
    ],
)
def test_geometry_digest_changes_with_geometry(variant: StructureRecord) -> None:
    """Atom order, coordinates, and element symbols are digest content."""
    assert variant.geometry_digest != record().geometry_digest


def test_same_geometry_different_identity() -> None:
    """Entity identity and content identity are separate axes."""
    first = structure("s1")
    second = structure("s2")
    assert first.geometry_digest == second.geometry_digest
    assert first != second
    assert first.has_same_content(second) is True


def test_scientific_payload_tracks_charge_and_multiplicity() -> None:
    """Charge and multiplicity move the science payload but not the geometry digest."""
    base = structure("s1", charge=0, multiplicity=1)
    charged = structure("s2", charge=1, multiplicity=2)
    assert base.geometry_digest == charged.geometry_digest
    assert base.scientific_payload() != charged.scientific_payload()
    assert base.scientific_payload()["charge"] == 0
    assert base.scientific_payload()["multiplicity"] == 1
    assert base.has_same_content(charged) is False


def test_structure_set_preserves_order_and_content_duplicates() -> None:
    """Identical content under different ids stays in the ordered set."""
    first = structure("s1")
    second = structure("s2")
    third = structure("s3")
    structure_set = StructureSet.of(first, second, third)
    assert structure_set.ids == ("s1", "s2", "s3")
    assert len(structure_set) == 3
    assert structure_set.is_empty is False
    assert structure_set.geometry_digests == (first.geometry_digest,) * 3


def test_structure_set_rejects_duplicate_id() -> None:
    """Entity ids are unique inside a set."""
    with pytest.raises(InvalidStructureError, match="duplicate structure id"):
        StructureSet.of(structure("s1"), structure("s1"))


def test_structure_set_lookup_helpers() -> None:
    """Lookup by id, role, group key, and lineage root is explicit."""
    first = structure("s1", role="reactant", group_key="g1", lineage_root_id="root")
    second = structure("s2", role="product", group_key="g2", lineage_root_id="root")
    structure_set = StructureSet.of(first, second)
    assert structure_set.by_id("s2") is second
    assert structure_set.get("missing") is None
    with pytest.raises(KeyError):
        structure_set.by_id("missing")
    assert structure_set.by_role("reactant").ids == ("s1",)
    assert structure_set.by_group_key("g2").ids == ("s2",)
    assert structure_set.by_lineage_root("root").ids == ("s1", "s2")
    assert structure_set.by_role("missing").is_empty is True


def test_structure_set_slicing_and_addition() -> None:
    """Slices return sets and addition rejects duplicate ids."""
    first = structure("s1")
    second = structure("s2")
    third = structure("s3")
    structure_set = StructureSet.of(first, second, third)
    assert structure_set[0] is first
    assert structure_set[1:].ids == ("s2", "s3")
    assert (structure_set[1:] + StructureSet.of(first)).ids == ("s2", "s3", "s1")
    with pytest.raises(InvalidStructureError, match="duplicate structure id"):
        StructureSet.of(first, second) + StructureSet.of(second, third)
