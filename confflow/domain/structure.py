#!/usr/bin/env python3

"""V4 structure identity model.

``StructureRecord.id`` is *entity identity*: it names the record inside a run
and stays stable across recompilation.  ``StructureRecord.geometry_digest`` is
*content identity*: a deterministic digest over atom order, coordinates, and
the canonical coordinate unit.  Two records may share a geometry digest while
having different ids, and the same id never silently points at changed
geometry.

Parent/lineage links use structure ids only; filenames and list positions are
never identity.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from numbers import Real
from typing import Any, Final, overload

from ._immutable import FrozenDict
from .canonical import typed_digest
from .elements import canonical_element_symbol
from .errors import ElementSymbolError, InvalidStructureError
from .units import Unit

__all__ = [
    "GEOMETRY_DIGEST_KIND",
    "Coordinates",
    "StructureRecord",
    "StructureSet",
]

#: Digest domain marker for :attr:`StructureRecord.geometry_digest`.
GEOMETRY_DIGEST_KIND: Final[str] = "confflow.structure.geometry.v1"

#: Immutable coordinate storage: ``((x, y, z), ...)`` in Ångström.
Coordinates = tuple[tuple[float, float, float], ...]


def _require_identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidStructureError(f"{field_name} must be a non-empty string")
    if value != value.strip():
        raise InvalidStructureError(f"{field_name} must not have surrounding whitespace")
    return value


def _normalize_atoms(atoms: Any) -> tuple[str, ...]:
    if isinstance(atoms, (str, bytes)) or not isinstance(atoms, Iterable):
        raise InvalidStructureError("atoms must be a sequence of element symbols")
    symbols = tuple(atoms)
    if not symbols:
        raise InvalidStructureError("atoms must not be empty")
    normalized: list[str] = []
    for index, symbol in enumerate(symbols):
        try:
            normalized.append(canonical_element_symbol(str(symbol)))
        except ElementSymbolError as exc:
            raise InvalidStructureError(f"atoms[{index}]: {exc}") from exc
    return tuple(normalized)


def _normalize_coordinates(coordinates: Any) -> Coordinates:
    if isinstance(coordinates, (str, bytes)) or not isinstance(coordinates, Iterable):
        raise InvalidStructureError("coordinates must be a sequence of (x, y, z) triples")
    points: list[tuple[float, float, float]] = []
    for point_index, point in enumerate(coordinates):
        if isinstance(point, (str, bytes)) or not isinstance(point, Iterable):
            raise InvalidStructureError(f"coordinates[{point_index}] must be an (x, y, z) triple")
        values = tuple(point)
        if len(values) != 3:
            raise InvalidStructureError(
                f"coordinates[{point_index}] must have exactly 3 values, got {len(values)}"
            )
        xyz: list[float] = []
        for axis, value in enumerate(values):
            if isinstance(value, bool) or not isinstance(value, Real):
                raise InvalidStructureError(
                    f"coordinates[{point_index}][{axis}] must be a real number"
                )
            number = float(value)
            if not math.isfinite(number):
                raise InvalidStructureError(f"coordinates[{point_index}][{axis}] must be finite")
            xyz.append(number)
        points.append((xyz[0], xyz[1], xyz[2]))
    if not points:
        raise InvalidStructureError("coordinates must not be empty")
    return tuple(points)


def _validate_optional_int(value: Any, field_name: str, *, minimum: int | None = None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidStructureError(f"{field_name} must be an integer or None")
    if minimum is not None and value < minimum:
        raise InvalidStructureError(f"{field_name} must be >= {minimum}")


@dataclass(frozen=True, slots=True)
class StructureRecord:
    """An immutable molecular structure record.

    Parameters
    ----------
    id : str
        Entity identity of this record.  Not derivable from the geometry.
    atoms : tuple[str, ...]
        Element symbols in atom order; canonicalized on construction.
    coordinates : Coordinates
        Cartesian coordinates in Ångström; frozen to nested tuples.
    charge : int | None
        Total charge when explicitly known.
    multiplicity : int | None
        Spin multiplicity when explicitly known; ``>= 1``.
    parent_ids : tuple[str, ...]
        Ids of the structures this record was derived from.
    lineage_root_id : str | None
        Root id of the provenance line; defaults to ``id``.
    source_step_id : str | None
        Step that produced this record, when applicable.
    source_work_item_id : str | None
        Work item that produced this record, when applicable.
    role : str | None
        Scientific role such as ``"reactant"`` or ``"product"``; a
        classification, never a dispatch switch.
    ordinal : int | None
        Producer-defined ordering within a collection (``>= 0``).
    group_key : str | None
        Explicit pairing key used by named-input assembly.  Producers that
        need deterministic pairing must set it; it is never inferred.
    metadata : FrozenDict
        Non-semantic annotations; excluded from :attr:`geometry_digest`.

    Raises
    ------
    InvalidStructureError
        Raised when any structural invariant is violated.
    """

    id: str
    atoms: tuple[str, ...]
    coordinates: Coordinates
    charge: int | None = None
    multiplicity: int | None = None
    parent_ids: tuple[str, ...] = ()
    lineage_root_id: str | None = None
    source_step_id: str | None = None
    source_work_item_id: str | None = None
    role: str | None = None
    ordinal: int | None = None
    group_key: str | None = None
    metadata: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        _require_identifier(self.id, "id")
        object.__setattr__(self, "atoms", _normalize_atoms(self.atoms))
        coordinates = _normalize_coordinates(self.coordinates)
        object.__setattr__(self, "coordinates", coordinates)
        if len(self.atoms) != len(coordinates):
            raise InvalidStructureError(
                f"atoms ({len(self.atoms)}) and coordinates ({len(coordinates)}) "
                "must have the same length"
            )
        _validate_optional_int(self.charge, "charge")
        _validate_optional_int(self.multiplicity, "multiplicity", minimum=1)
        _validate_optional_int(self.ordinal, "ordinal", minimum=0)

        parents = tuple(self.parent_ids)
        for index, parent_id in enumerate(parents):
            _require_identifier(parent_id, f"parent_ids[{index}]")
        if self.id in parents:
            raise InvalidStructureError("a structure must not list itself as parent")
        object.__setattr__(self, "parent_ids", parents)

        if self.lineage_root_id is None:
            object.__setattr__(self, "lineage_root_id", self.id)
        else:
            _require_identifier(self.lineage_root_id, "lineage_root_id")
        for name in ("source_step_id", "source_work_item_id", "role"):
            value = getattr(self, name)
            if value is not None:
                _require_identifier(value, name)
        if self.group_key is not None:
            _require_identifier(self.group_key, "group_key")
        if not isinstance(self.metadata, FrozenDict):
            object.__setattr__(self, "metadata", FrozenDict(self.metadata))

    @property
    def geometry_digest(self) -> str:
        """Return the deterministic digest of the geometry content.

        The digest covers atom order, coordinates, and the canonical unit only.
        Identity, provenance, charge, multiplicity, and metadata are excluded
        so that identical geometries share content identity.
        """
        return typed_digest(
            GEOMETRY_DIGEST_KIND,
            {
                "atoms": self.atoms,
                "coordinates": self.coordinates,
                "unit": Unit.ANGSTROM,
            },
        )

    def scientific_payload(self) -> dict[str, Any]:
        """Return this structure's contribution to a work-item digest.

        The payload carries everything that can change computed results
        without changing the geometry: geometry content, charge, and
        multiplicity.  Entity id and provenance are deliberately excluded so
        that identical scientific content re-imported under new entity ids
        keeps the same reuse identity.
        """
        return {
            "geometry_digest": self.geometry_digest,
            "charge": self.charge,
            "multiplicity": self.multiplicity,
        }

    def has_same_content(self, other: StructureRecord) -> bool:
        """Return whether *other* has identical scientific content.

        Content identity compares geometry, charge, and multiplicity; entity
        ids, parentage, roles, and metadata are ignored.
        """
        return isinstance(other, StructureRecord) and (
            self.scientific_payload() == other.scientific_payload()
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "id": self.id,
            "atoms": list(self.atoms),
            "coordinates": [list(point) for point in self.coordinates],
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "parent_ids": list(self.parent_ids),
            "lineage_root_id": self.lineage_root_id,
            "source_step_id": self.source_step_id,
            "source_work_item_id": self.source_work_item_id,
            "role": self.role,
            "ordinal": self.ordinal,
            "group_key": self.group_key,
            "metadata": self.metadata.thaw(),
            "geometry_digest": self.geometry_digest,
        }


@dataclass(frozen=True, slots=True)
class StructureSet:
    """An ordered, immutable collection of structure records.

    A structure set may represent a single structure, unrelated structures, a
    conformer ensemble, reaction endpoints, or a named-result collection.  The
    engine never infers workflow semantics from its size.
    """

    structures: tuple[StructureRecord, ...] = ()

    def __post_init__(self) -> None:
        records = tuple(self.structures)
        seen: set[str] = set()
        for record in records:
            if not isinstance(record, StructureRecord):
                raise InvalidStructureError(
                    f"StructureSet members must be StructureRecord, got {type(record).__name__}"
                )
            if record.id in seen:
                raise InvalidStructureError(f"duplicate structure id in set: {record.id!r}")
            seen.add(record.id)
        object.__setattr__(self, "structures", records)

    def __iter__(self) -> Iterator[StructureRecord]:
        return iter(self.structures)

    def __len__(self) -> int:
        return len(self.structures)

    @overload
    def __getitem__(self, index: int) -> StructureRecord: ...

    @overload
    def __getitem__(self, index: slice) -> StructureSet: ...

    def __getitem__(self, index: int | slice) -> StructureRecord | StructureSet:
        if isinstance(index, slice):
            return StructureSet(self.structures[index])
        return self.structures[index]

    @property
    def ids(self) -> tuple[str, ...]:
        """Return structure ids in set order."""
        return tuple(record.id for record in self.structures)

    @property
    def geometry_digests(self) -> tuple[str, ...]:
        """Return geometry content digests in set order."""
        return tuple(record.geometry_digest for record in self.structures)

    @property
    def is_empty(self) -> bool:
        """Return whether the set contains no structures."""
        return not self.structures

    @classmethod
    def of(cls, *structures: StructureRecord) -> StructureSet:
        """Build a set from individual records."""
        return cls(tuple(structures))

    def by_id(self, structure_id: str) -> StructureRecord:
        """Return the record with *structure_id*.

        Raises
        ------
        KeyError
            Raised when the id is not present in this set.
        """
        for record in self.structures:
            if record.id == structure_id:
                return record
        raise KeyError(structure_id)

    def get(self, structure_id: str) -> StructureRecord | None:
        """Return the record with *structure_id*, or ``None``."""
        for record in self.structures:
            if record.id == structure_id:
                return record
        return None

    def by_role(self, role: str) -> StructureSet:
        """Return the sub-set of records whose role equals *role*."""
        return StructureSet(tuple(record for record in self.structures if record.role == role))

    def by_group_key(self, group_key: str) -> StructureSet:
        """Return the sub-set of records whose group key equals *group_key*."""
        return StructureSet(
            tuple(record for record in self.structures if record.group_key == group_key)
        )

    def by_lineage_root(self, lineage_root_id: str) -> StructureSet:
        """Return the sub-set of records sharing *lineage_root_id*."""
        return StructureSet(
            tuple(record for record in self.structures if record.lineage_root_id == lineage_root_id)
        )

    def __add__(self, other: StructureSet) -> StructureSet:
        if not isinstance(other, StructureSet):
            raise TypeError(f"cannot add {type(other).__name__} to StructureSet")
        return StructureSet(self.structures + other.structures)
