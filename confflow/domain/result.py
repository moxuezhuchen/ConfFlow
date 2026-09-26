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
    "RESULT_IDENTITY_DIGEST_KIND",
    "RESULT_VALUE_DIGEST_KIND",
    "Provenance",
    "ResultRef",
    "ResultSet",
    "ScientificResult",
    "find_duplicate_result_ids",
    "make_result_id",
    "require_production_ids",
]

#: Digest domain marker for :attr:`ScientificResult.value_digest`.
RESULT_VALUE_DIGEST_KIND: Final[str] = "confflow.result.value.v1"

#: Digest domain marker for deterministic production result identity.
RESULT_IDENTITY_DIGEST_KIND: Final[str] = "confflow.result.identity.v1"


def _require_identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidResultError(f"{field_name} must be a non-empty string")
    if value != value.strip():
        raise InvalidResultError(f"{field_name} must not have surrounding whitespace")
    return value


def _require_producer_digest(value: Any) -> str:
    """Validate a producer work-item semantic digest.

    Must be the producing item's ``semantic_digest`` verbatim
    (``sha256:<hex>``); anything else fails closed rather than minting an
    identity unbound to the actual scientific generation.
    """
    if (
        not isinstance(value, str)
        or not value.startswith("sha256:")
        or len(value) != 71
        or any(c not in "0123456789abcdef" for c in value[7:])
    ):
        raise InvalidResultError(
            "producer_digest must be the producing work item's semantic_digest "
            f"(sha256:<hex>), got {value!r}"
        )
    return value


@dataclass(frozen=True, slots=True)
class ResultRef:
    """A typed, independent reference to one production scientific result.

    A ``ResultRef`` names a result entity, never its value: two records with
    equal values but different producers/subjects/kinds carry different refs.
    The ``result_id`` is producer-scoped and stable; see
    :func:`make_result_id` for deterministic construction.
    """

    result_id: str

    def __post_init__(self) -> None:
        _require_identifier(self.result_id, "result_id")

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {"result_id": self.result_id}


def make_result_id(
    *,
    step_id: str,
    work_item_id: str | None = None,
    kind: str,
    subject_structure_id: str | None = None,
    discriminator: str | None = None,
    program: str | None = None,
    method: str | None = None,
    basis: str | None = None,
    producer_digest: str,
) -> str:
    """Return a deterministic producer-scoped result id.

    The id is derived from complete producer/subject/kind semantic data so
    distinct producers, subjects, kinds, or discriminators never collapse.
    ``producer_digest`` is the producing work item's ``semantic_digest``
    passed verbatim (C stamps it directly on emission): retries or
    reordering of the same semantic item retain refs, while any changed
    science (solvent, native settings, inputs) moves the producer digest
    and therefore mints new refs.  Method/basis strings alone are NOT a
    substitute — they alias across changed scientific settings.  Value
    equality and paths/ordinals never participate: equal values from
    different sources mint different ids.
    """
    _require_identifier(step_id, "step_id")
    _require_identifier(kind, "kind")
    if work_item_id is not None:
        _require_identifier(work_item_id, "work_item_id")
    if subject_structure_id is not None:
        _require_identifier(subject_structure_id, "subject_structure_id")
    if discriminator is not None:
        _require_identifier(discriminator, "discriminator")
    for name, value in (("program", program), ("method", method), ("basis", basis)):
        if value is not None:
            _require_identifier(value, name)
    _require_producer_digest(producer_digest)
    return typed_digest(
        RESULT_IDENTITY_DIGEST_KIND,
        {
            "step_id": step_id,
            "work_item_id": work_item_id,
            "kind": kind,
            "subject_structure_id": subject_structure_id,
            "discriminator": discriminator,
            "program": program,
            "method": method,
            "basis": basis,
            "producer_digest": producer_digest,
        },
    )


def require_production_ids(results: ResultSet) -> None:
    """Reject read-only legacy records masquerading as production results.

    Every record in *results* must carry a stable producer-scoped
    ``result_id``; legacy records with ``result_id=None`` fail closed and
    must never silently become valid production refs.  Profile (C) and
    analysis (F) emission paths call this before publishing: stamping is
    their job, gating is this helper's.
    """
    for record in results:
        if record.result_id is None:
            raise InvalidResultError(
                f"production result kind={record.kind!r} "
                f"subject={record.subject_structure_id!r} carries no result_id; "
                "stamp a producer-scoped id via make_result_id before publication"
            )


def find_duplicate_result_ids(results: ResultSet) -> tuple[str, ...]:
    """Return sorted ``result_id`` values claimed by more than one record.

    Same value across producers is not the same entity: two source records
    must never collapse solely because their values match, so production
    siblings carry distinct ids.  A repeated id for one entity's slot is a
    collision the caller must fail closed on (see ``ResultSet.select_ids``
    for per-requested-id ambiguity at consumption time).
    """
    seen: set[str] = set()
    duplicates: set[str] = set()
    for record in results:
        if record.result_id is None:
            continue
        if record.result_id in seen:
            duplicates.add(record.result_id)
        seen.add(record.result_id)
    return tuple(sorted(duplicates))


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
    result_id: str | None = None
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
        if self.result_id is not None:
            _require_identifier(self.result_id, "result_id")
        if not isinstance(self.metadata, FrozenDict):
            object.__setattr__(self, "metadata", FrozenDict(self.metadata))

    @property
    def value_digest(self) -> str:
        """Return the content digest of this result value and unit."""
        return typed_digest(
            RESULT_VALUE_DIGEST_KIND,
            {"kind": self.kind, "value": self.value, "unit": self.unit},
        )

    @property
    def ref(self) -> ResultRef | None:
        """Return the independent identity reference, or ``None`` for legacy."""
        if self.result_id is None:
            return None
        return ResultRef(result_id=self.result_id)

    @property
    def identity_digest(self) -> str | None:
        """Return the producer-scoped identity digest, or ``None`` for legacy.

        Identity covers the stable ``result_id`` plus producer context
        (step, work item, subject, kind, provenance): distinct producers,
        methods, or subjects are distinguishable even when values match.
        The value itself never participates beyond the kind discriminator.
        """
        if self.result_id is None:
            return None
        return typed_digest(
            RESULT_IDENTITY_DIGEST_KIND,
            {
                "result_id": self.result_id,
                "kind": self.kind,
                "subject_structure_id": self.subject_structure_id,
                "source_step_id": self.source_step_id,
                "source_work_item_id": self.source_work_item_id,
                "provenance": (self.provenance.to_dict() if self.provenance is not None else None),
            },
        )

    @property
    def is_production(self) -> bool:
        """Return whether this result carries a production identity."""
        return self.result_id is not None

    def digest_payload(self) -> dict[str, Any]:
        """Return this result's contribution to a work-item digest.

        The value digest stays value-only; identity (``result_id``) and
        producer provenance are scientifically relevant and participate
        separately so same values from different sources digest differently.
        """
        return {
            "kind": self.kind,
            "value_digest": self.value_digest,
            "subject_structure_id": self.subject_structure_id,
            "result_id": self.result_id,
            "identity_digest": self.identity_digest,
            "provenance": (self.provenance.to_dict() if self.provenance is not None else None),
            "source_step_id": self.source_step_id,
            "source_work_item_id": self.source_work_item_id,
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
            "result_id": self.result_id,
            "identity_digest": self.identity_digest,
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

    def by_result_id(self, result_id: str) -> ResultSet:
        """Return the sub-set of results carrying *result_id*."""
        return ResultSet(tuple(result for result in self.results if result.result_id == result_id))

    def select_ids(
        self,
        ids: tuple[str, ...] | list[str],
    ) -> tuple[ResultSet, tuple[str, ...], tuple[str, ...]]:
        """Select results by independent ``result_id`` references.

        Each requested id is resolved independently: zero candidates for
        one id is ``missing``; more than one candidate for the *same*
        requested id is ``ambiguous`` (duplicate production ids fail).
        Distinct requested ids may each resolve to one result, so a MANY
        port may legitimately select several ids.  No global
        exactly-one-result requirement is imposed here.
        """
        requested = tuple(ids)
        selected: list[ScientificResult] = []
        missing: list[str] = []
        ambiguous: list[str] = []
        for requested_id in requested:
            matches = [r for r in self.results if r.result_id == requested_id]
            if not matches:
                missing.append(requested_id)
            elif len(matches) > 1:
                ambiguous.append(requested_id)
            else:
                selected.append(matches[0])
        return ResultSet(tuple(selected)), tuple(missing), tuple(ambiguous)

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

    def select_unique(
        self,
        kind: str,
        *,
        subject_structure_id: str | None = None,
        result_id: str | None = None,
    ) -> ScientificResult:
        """Return the uniquely selected result or raise ``InvalidResultError``.

        Zero candidates and multiple ambiguous candidates both fail: callers
        must provide a ``result_id`` (or equivalent selector) when several
        results share kind/subject.  Positional first-match is never a
        substitute for unique selection.
        """
        candidates = [r for r in self.results if r.kind == kind]
        if subject_structure_id is not None:
            candidates = [r for r in candidates if r.subject_structure_id == subject_structure_id]
        if result_id is not None:
            candidates = [r for r in candidates if r.result_id == result_id]
        if not candidates:
            raise InvalidResultError(
                f"no result matches kind={kind!r} subject={subject_structure_id!r} id={result_id!r}"
            )
        if len(candidates) > 1:
            raise InvalidResultError(
                f"ambiguous result selection for kind={kind!r} subject={subject_structure_id!r}: "
                f"{len(candidates)} candidates; narrow with an explicit result_id"
            )
        return candidates[0]

    def __add__(self, other: ResultSet) -> ResultSet:
        if not isinstance(other, ResultSet):
            raise TypeError(f"cannot add {type(other).__name__} to ResultSet")
        return ResultSet(self.results + other.results)
