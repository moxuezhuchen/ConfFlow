#!/usr/bin/env python3

"""V4 binding model: the only dataflow edge vocabulary.

Bindings connect a named target port to a stable source identity: a run input
name or a ``(step id, port)`` pair, optionally narrowed by an explicit
selector.  Labels, filenames, and list indices are never selectors.

Pairing semantics (how per-item values are matched) are declared, never
inferred: ``single`` broadcasts one value, ``per_structure`` drives work-item
fan-out, ``by_subject`` matches by ``subject_structure_id``, and
``by_group_key`` matches by an explicit producer-assigned group key.  The
compiler resolves a missing pairing from the port contract; ambiguity is a
compile error, never a guess.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, overload

from .errors import InvalidBindingError

__all__ = [
    "Binding",
    "BindingSet",
    "BindingSource",
    "Cardinality",
    "Pairing",
    "PartialConsumption",
    "PortKind",
    "PortSelector",
    "SelectorKind",
    "SourceKind",
]


class PortKind(str, Enum):
    """Kinds of named ports in the binding graph."""

    STRUCTURE = "structure"
    ARTIFACT = "artifact"
    RESULT = "result"


class SourceKind(str, Enum):
    """Kinds of binding sources."""

    RUN_INPUT = "run_input"
    STEP_OUTPUT = "step_output"


class Cardinality(str, Enum):
    """Cardinality contract of a target port.

    Attributes
    ----------
    ONE
        Exactly one value; missing or multiple values is an error.
    OPTIONAL
        Zero or one value.
    ONE_OR_MORE
        At least one value.
    MANY
        Zero or more values.
    """

    ONE = "one"
    OPTIONAL = "optional"
    ONE_OR_MORE = "one_or_more"
    MANY = "many"


class Pairing(str, Enum):
    """How source values are matched to work items.

    Attributes
    ----------
    SINGLE
        One value (or set) shared by every work item of the step.
    PER_STRUCTURE
        One value per structure; drives work-item fan-out.
    BY_SUBJECT
        Match values to the driving structure by ``subject_structure_id``.
    BY_GROUP_KEY
        Match values by explicit producer-assigned group key.
    """

    SINGLE = "single"
    PER_STRUCTURE = "per_structure"
    BY_SUBJECT = "by_subject"
    BY_GROUP_KEY = "by_group_key"


class PartialConsumption(str, Enum):
    """Whether a consumer may consume an ``allow_partial`` producer's subset.

    Attributes
    ----------
    REQUIRE_COMPLETE
        The consumer is not scheduled unless the producer fully completed.
    ACCEPT_SUBSET
        The consumer may be materialized from the accepted completed subset.
    """

    REQUIRE_COMPLETE = "require_complete"
    ACCEPT_SUBSET = "accept_subset"


class SelectorKind(str, Enum):
    """Kinds of source-port selectors."""

    ALL = "all"
    ROLE = "role"
    IDS = "ids"


def _require_identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidBindingError(f"{field_name} must be a non-empty string")
    if value != value.strip():
        raise InvalidBindingError(f"{field_name} must not have surrounding whitespace")
    return value


@dataclass(frozen=True, slots=True)
class PortSelector:
    """An explicit selector narrowing a source port.

    Parameters
    ----------
    kind : SelectorKind
        Selector kind.
    role : str | None
        Artifact role for ``ROLE`` selectors.
    ids : tuple[str, ...]
        Stable ids for ``IDS`` selectors.

    Raises
    ------
    InvalidBindingError
        Raised when the fields do not match the selector kind.
    """

    kind: SelectorKind = SelectorKind.ALL
    role: str | None = None
    ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SelectorKind):
            raise InvalidBindingError("selector kind must be a SelectorKind")
        ids = tuple(self.ids)
        if self.kind is SelectorKind.ALL:
            if self.role is not None or ids:
                raise InvalidBindingError("ALL selector must not carry role or ids")
        elif self.kind is SelectorKind.ROLE:
            if self.role is None:
                raise InvalidBindingError("ROLE selector requires a role")
            _require_identifier(self.role, "selector role")
            if ids:
                raise InvalidBindingError("ROLE selector must not carry ids")
        elif self.kind is SelectorKind.IDS:
            if not ids:
                raise InvalidBindingError("IDS selector requires at least one id")
            if self.role is not None:
                raise InvalidBindingError("IDS selector must not carry a role")
            for index, item in enumerate(ids):
                _require_identifier(item, f"selector ids[{index}]")
        object.__setattr__(self, "ids", ids)

    @classmethod
    def all(cls) -> PortSelector:
        """Return the unrestricted selector."""
        return cls(SelectorKind.ALL)

    @classmethod
    def by_role(cls, role: str) -> PortSelector:
        """Return a selector matching one artifact role."""
        return cls(SelectorKind.ROLE, role=role)

    @classmethod
    def by_ids(cls, *ids: str) -> PortSelector:
        """Return a selector matching stable ids."""
        return cls(SelectorKind.IDS, ids=tuple(ids))

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {"kind": self.kind.value, "role": self.role, "ids": list(self.ids)}


@dataclass(frozen=True, slots=True)
class BindingSource:
    """The upstream end of a binding edge.

    Parameters
    ----------
    kind : SourceKind
        Whether the source is a run input or a step output.
    port : str
        Run input name or producer output port name.
    step_id : str | None
        Producer step id; forbidden for run inputs.
    selector : PortSelector
        Explicit narrowing of the source port.

    Raises
    ------
    InvalidBindingError
        Raised when the source kind and step id disagree.
    """

    kind: SourceKind
    port: str
    step_id: str | None = None
    selector: PortSelector = field(default_factory=PortSelector)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SourceKind):
            raise InvalidBindingError("source kind must be a SourceKind")
        _require_identifier(self.port, "source port")
        if self.kind is SourceKind.STEP_OUTPUT:
            if self.step_id is None:
                raise InvalidBindingError("step-output source requires a step id")
            _require_identifier(self.step_id, "source step id")
        elif self.step_id is not None:
            raise InvalidBindingError("run-input source must not carry a step id")
        if not isinstance(self.selector, PortSelector):
            raise InvalidBindingError("source selector must be a PortSelector")

    @classmethod
    def run_input(cls, name: str, selector: PortSelector | None = None) -> BindingSource:
        """Build a run-input source."""
        return cls(SourceKind.RUN_INPUT, name, selector=selector or PortSelector.all())

    @classmethod
    def step_output(
        cls, step_id: str, port: str, selector: PortSelector | None = None
    ) -> BindingSource:
        """Build a step-output source."""
        return cls(
            SourceKind.STEP_OUTPUT,
            port,
            step_id=step_id,
            selector=selector or PortSelector.all(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "kind": self.kind.value,
            "port": self.port,
            "step_id": self.step_id,
            "selector": self.selector.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class Binding:
    """A single dataflow edge into a named target port.

    Parameters
    ----------
    target_step_id : str
        Step whose port receives the data.
    target_port : str
        Declared input port on the target step.
    source : BindingSource
        Stable upstream identity.
    cardinality : Cardinality | None
        How many values the target port accepts; ``None`` defers to the port
        contract's declared cardinality.
    pairing : Pairing | None
        Pairing semantics; ``None`` defers to the port contract default.
    partial_consumption : PartialConsumption | None
        Declaration required when the producer uses ``allow_partial``.

    Raises
    ------
    InvalidBindingError
        Raised when identifiers are empty or a step binds its own output.
    """

    target_step_id: str
    target_port: str
    source: BindingSource
    cardinality: Cardinality | None = None
    pairing: Pairing | None = None
    partial_consumption: PartialConsumption | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.target_step_id, "target step id")
        _require_identifier(self.target_port, "target port")
        if not isinstance(self.source, BindingSource):
            raise InvalidBindingError("binding source must be a BindingSource")
        if self.cardinality is not None and not isinstance(self.cardinality, Cardinality):
            raise InvalidBindingError("cardinality must be a Cardinality or None")
        if self.pairing is not None and not isinstance(self.pairing, Pairing):
            raise InvalidBindingError("pairing must be a Pairing or None")
        if self.source.kind is SourceKind.STEP_OUTPUT and (
            self.source.step_id == self.target_step_id
        ):
            raise InvalidBindingError("a step must not bind its own output")
        if self.partial_consumption is not None and not isinstance(
            self.partial_consumption, PartialConsumption
        ):
            raise InvalidBindingError("partial_consumption must be a PartialConsumption or None")

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "target_step_id": self.target_step_id,
            "target_port": self.target_port,
            "source": self.source.to_dict(),
            "cardinality": self.cardinality.value if self.cardinality is not None else None,
            "pairing": self.pairing.value if self.pairing is not None else None,
            "partial_consumption": (
                self.partial_consumption.value if self.partial_consumption is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class BindingSet:
    """An immutable set of bindings.

    V4-1 allows at most one binding per ``(step id, port)`` pair: aggregating
    multiple sources into one port requires an explicit gather step, so the
    compiler never has to guess how to merge values.

    Raises
    ------
    InvalidBindingError
        Raised on duplicate target ports.
    """

    bindings: tuple[Binding, ...] = ()

    def __post_init__(self) -> None:
        records = tuple(self.bindings)
        seen: set[tuple[str, str]] = set()
        for record in records:
            if not isinstance(record, Binding):
                raise InvalidBindingError(
                    f"BindingSet members must be Binding, got {type(record).__name__}"
                )
            key = (record.target_step_id, record.target_port)
            if key in seen:
                raise InvalidBindingError(f"duplicate binding for target port {key[0]}.{key[1]}")
            seen.add(key)
        object.__setattr__(self, "bindings", records)

    def __iter__(self) -> Iterator[Binding]:
        return iter(self.bindings)

    def __len__(self) -> int:
        return len(self.bindings)

    @overload
    def __getitem__(self, index: int) -> Binding: ...

    @overload
    def __getitem__(self, index: slice) -> BindingSet: ...

    def __getitem__(self, index: int | slice) -> Binding | BindingSet:
        if isinstance(index, slice):
            return BindingSet(self.bindings[index])
        return self.bindings[index]

    @property
    def is_empty(self) -> bool:
        """Return whether the set contains no bindings."""
        return not self.bindings

    @classmethod
    def of(cls, *bindings: Binding) -> BindingSet:
        """Build a set from individual bindings."""
        return cls(tuple(bindings))

    def by_target(self, step_id: str) -> BindingSet:
        """Return the bindings whose target is *step_id*."""
        return BindingSet(
            tuple(record for record in self.bindings if record.target_step_id == step_id)
        )

    def incoming(self, step_id: str) -> BindingSet:
        """Alias of :meth:`by_target`."""
        return self.by_target(step_id)

    def outgoing(self, step_id: str) -> BindingSet:
        """Return the bindings whose source step is *step_id*."""
        return BindingSet(
            tuple(
                record
                for record in self.bindings
                if record.source.kind is SourceKind.STEP_OUTPUT and record.source.step_id == step_id
            )
        )

    def for_port(self, step_id: str, port: str) -> Binding | None:
        """Return the binding targeting ``(step_id, port)``, if any."""
        for record in self.bindings:
            if record.target_step_id == step_id and record.target_port == port:
                return record
        return None

    def target_steps(self) -> tuple[str, ...]:
        """Return target step ids in binding order, de-duplicated."""
        ordered: list[str] = []
        for record in self.bindings:
            if record.target_step_id not in ordered:
                ordered.append(record.target_step_id)
        return tuple(ordered)

    def __add__(self, other: BindingSet) -> BindingSet:
        if not isinstance(other, BindingSet):
            raise TypeError(f"cannot add {type(other).__name__} to BindingSet")
        return BindingSet(self.bindings + other.bindings)
