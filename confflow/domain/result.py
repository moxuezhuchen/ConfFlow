#!/usr/bin/env python3

"""V4 scientific result model.

Results carry a ``kind`` (an open, program-defined vocabulary such as
``"energy"`` or ``"frequencies"``), a canonical JSON value, and an explicit
unit.  The domain layer never assumes implicit units and never stores fixed
chemistry columns: a ResultSet is a typed collection, not a database row.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Final, overload

from ._immutable import FrozenDict
from .canonical import normalize, typed_digest
from .errors import CanonicalizationError, InvalidResultError
from .units import UNIT_QUANTITIES, QuantityKind, Unit

__all__ = [
    "RESULT_VALUE_DIGEST_KIND",
    "Provenance",
    "ResultSet",
    "ScientificResult",
]

#: Digest domain marker for :attr:`ScientificResult.value_digest`.
RESULT_VALUE_DIGEST_KIND: Final[str] = "confflow.result.value.v1"


def _require_identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidResultError(f"{field_name} must be a non-empty string")
    if value != value.strip():
        raise InvalidResultError(f"{field_name} must not have surrounding whitespace")
    return value


@dataclass(frozen=True, slots=True)
class Provenance:
    """Producer provenance for a scientific result.

    Provenance records *where a value came from*; it never participates in the
    value digest itself, so identical values from different producers compare
    equal.
    """

    program: str | None = None
    program_version: str | None = None
    method: str | None = None
    basis: str | None = None
    adapter: str | None = None
    step_id: str | None = None
    work_item_id: str | None = None
    metadata: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        for name in (
            "program",
            "program_version",
            "method",
            "basis",
            "adapter",
            "step_id",
            "work_item_id",
        ):
            value = getattr(self, name)
            if value is not None:
                _require_identifier(value, name)
        if not isinstance(self.metadata, FrozenDict):
            object.__setattr__(self, "metadata", FrozenDict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "program": self.program,
            "program_version": self.program_version,
            "method": self.method,
            "basis": self.basis,
            "adapter": self.adapter,
            "step_id": self.step_id,
            "work_item_id": self.work_item_id,
            "metadata": self.metadata.thaw(),
        }


@dataclass(frozen=True, slots=True)
class ScientificResult:
    """An immutable scientific value with explicit units.

    Parameters
    ----------
    kind : str
        Result kind such as ``"energy"`` or ``"frequencies"``.
    value : Any
        Canonical JSON-compatible value; validated and normalized on
        construction.
    unit : Unit | None
        Explicit unit.  Required whenever *quantity* is declared.
    quantity : QuantityKind | None
        Physical quantity kind; inferred from *unit* when omitted.
    subject_structure_id : str | None
        Structure the result belongs to, for subject matching.
    source_step_id : str | None
        Step that produced the result.
    source_work_item_id : str | None
        Work item that produced the result.
    provenance : Provenance | None
        Producer provenance.
    metadata : FrozenDict
        Non-semantic annotations.

    Raises
    ------
    InvalidResultError
        Raised when units are implicit or inconsistent, or the value is not
        canonically representable.
    """

    kind: str
    value: Any
    unit: Unit | None = None
    quantity: QuantityKind | None = None
    subject_structure_id: str | None = None
    source_step_id: str | None = None
    source_work_item_id: str | None = None
    provenance: Provenance | None = None
    metadata: FrozenDict = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        _require_identifier(self.kind, "kind")
        unit: Unit | None = None
        if self.unit is not None:
            if not isinstance(self.unit, Unit):
                raise InvalidResultError("unit must be a Unit")
            unit = self.unit
        quantity: QuantityKind | None = None
        if self.quantity is not None:
            if not isinstance(self.quantity, QuantityKind):
                raise InvalidResultError("quantity must be a QuantityKind")
            quantity = self.quantity
        if unit is not None and quantity is None:
            inferred = UNIT_QUANTITIES.get(unit)
            if inferred is None:
                raise InvalidResultError(f"unknown unit: {unit.value!r}")
            object.__setattr__(self, "quantity", inferred)
        elif quantity is not None and unit is None:
            raise InvalidResultError(f"quantity {quantity.value!r} requires an explicit unit")
        elif unit is not None and quantity is not None:
            expected = UNIT_QUANTITIES.get(unit)
            if expected is not quantity:
                raise InvalidResultError(f"unit {unit.value!r} does not measure {quantity.value!r}")
        try:
            object.__setattr__(self, "value", normalize(self.value))
        except CanonicalizationError as exc:
            raise InvalidResultError(f"result value is not canonical: {exc}") from exc
        for name in ("subject_structure_id", "source_step_id", "source_work_item_id"):
            value = getattr(self, name)
            if value is not None:
                _require_identifier(value, name)
        if self.provenance is not None and not isinstance(self.provenance, Provenance):
            raise InvalidResultError("provenance must be a Provenance")
        if not isinstance(self.metadata, FrozenDict):
            object.__setattr__(self, "metadata", FrozenDict(self.metadata))

    @property
    def value_digest(self) -> str:
        """Return the content digest of this result value and unit."""
        return typed_digest(
            RESULT_VALUE_DIGEST_KIND,
            {"kind": self.kind, "value": self.value, "unit": self.unit},
        )

    def digest_payload(self) -> dict[str, Any]:
        """Return this result's contribution to a work-item digest."""
        return {
            "kind": self.kind,
            "value_digest": self.value_digest,
            "subject_structure_id": self.subject_structure_id,
        }

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "kind": self.kind,
            "value": self.value,
            "unit": self.unit.value if self.unit is not None else None,
            "quantity": self.quantity.value if self.quantity is not None else None,
            "subject_structure_id": self.subject_structure_id,
            "source_step_id": self.source_step_id,
            "source_work_item_id": self.source_work_item_id,
            "provenance": self.provenance.to_dict() if self.provenance is not None else None,
            "metadata": self.metadata.thaw(),
            "value_digest": self.value_digest,
        }


@dataclass(frozen=True, slots=True)
class ResultSet:
    """An ordered, immutable collection of scientific results."""

    results: tuple[ScientificResult, ...] = ()

    def __post_init__(self) -> None:
        records = tuple(self.results)
        for record in records:
            if not isinstance(record, ScientificResult):
                raise InvalidResultError(
                    f"ResultSet members must be ScientificResult, got {type(record).__name__}"
                )
        object.__setattr__(self, "results", records)

    def __iter__(self) -> Iterator[ScientificResult]:
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)

    @overload
    def __getitem__(self, index: int) -> ScientificResult: ...

    @overload
    def __getitem__(self, index: slice) -> ResultSet: ...

    def __getitem__(self, index: int | slice) -> ScientificResult | ResultSet:
        if isinstance(index, slice):
            return ResultSet(self.results[index])
        return self.results[index]

    @property
    def is_empty(self) -> bool:
        """Return whether the set contains no results."""
        return not self.results

    @classmethod
    def of(cls, *results: ScientificResult) -> ResultSet:
        """Build a set from individual results."""
        return cls(tuple(results))

    def by_kind(self, kind: str) -> ResultSet:
        """Return the sub-set of results whose kind equals *kind*."""
        return ResultSet(tuple(result for result in self.results if result.kind == kind))

    def by_subject(self, subject_structure_id: str) -> ResultSet:
        """Return the sub-set of results bound to *subject_structure_id*."""
        return ResultSet(
            tuple(
                result
                for result in self.results
                if result.subject_structure_id == subject_structure_id
            )
        )

    def first(self, kind: str, subject_structure_id: str | None = None) -> ScientificResult | None:
        """Return the first result matching *kind* and optional subject."""
        for result in self.results:
            if result.kind != kind:
                continue
            if subject_structure_id is not None and result.subject_structure_id != (
                subject_structure_id
            ):
                continue
            return result
        return None

    def __add__(self, other: ResultSet) -> ResultSet:
        if not isinstance(other, ResultSet):
            raise TypeError(f"cannot add {type(other).__name__} to ResultSet")
        return ResultSet(self.results + other.results)
