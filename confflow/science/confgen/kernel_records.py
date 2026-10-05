#!/usr/bin/env python3

"""Generic kernel record types (FIX-1A A2).

Component-agnostic records used inside the engine. No v3 axis names,
no ``wire_v3`` imports, no component-id validation (the engine checks
``registry.ids()`` at construction sites).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from confflow.domain._immutable import FrozenDict

__all__ = [
    "ComponentStateKey",
    "InheritedScopeError",
    "KernelGenerationTarget",
    "KernelRun",
    "KernelWorkingRealization",
    "TopologyBuildContext",
    "as_kernel_target",
]


class InheritedScopeError(ValueError):
    """Generic, component-agnostic error for unauditable chained state."""


def _freeze_json(value: Any, *, path: str = "$") -> Any:
    """Defensively copy *value* into plain JSON-compatible containers."""
    if value is None or isinstance(value, (bool, int, str)):
        if isinstance(value, float):
            raise ValueError(f"{path} must hold finite JSON values")
        return value
    if isinstance(value, float):
        import math

        if not math.isfinite(value):
            raise ValueError(f"{path} holds a non-finite float")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} mapping keys must be strings")
            frozen[key] = _freeze_json(item, path=f"{path}.{key}")
        return frozen
    if isinstance(value, (list, tuple)):
        return [_freeze_json(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise ValueError(f"{path} holds a non-JSON-compatible value of type {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class ComponentStateKey:
    """Generic physical-state identity: arbitrary component ids."""

    components: Mapping[str, Any] = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        if not isinstance(self.components, Mapping):
            raise ValueError("components must be a mapping")
        raw = dict(self.components)
        frozen = _freeze_json(raw, path="$.components")
        if not isinstance(frozen, dict):
            raise ValueError("components must be a mapping")
        object.__setattr__(self, "components", FrozenDict(frozen))

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical JSON-compatible generic representation."""
        return {
            "components": {key: _freeze_json(val) for key, val in dict(self.components).items()},
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ComponentStateKey:
        """Rebuild a generic key from :meth:`to_dict` output (fail closed)."""
        if not isinstance(payload, Mapping):
            raise ValueError("component state key payload must be a mapping")
        components = payload.get("components", {})
        if not isinstance(components, Mapping):
            raise ValueError("component state key components must be a mapping")
        return cls(components=_freeze_json(dict(components), path="$.components"))


@dataclass(frozen=True, slots=True)
class KernelGenerationTarget:
    """Symbolic enumeration target with a generic (non-empty) axis."""

    axis: str
    target_id: str
    state_value: Mapping[str, Any]
    ordinal: int
    provenance: Mapping[str, Any] = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        if not isinstance(self.axis, str) or not self.axis:
            raise ValueError("target axis must be a non-empty string")
        if not isinstance(self.target_id, str) or not self.target_id:
            raise ValueError("target_id must be a non-empty string")
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 0:
            raise ValueError("ordinal must be an integer >= 0")
        frozen = _freeze_json(dict(self.state_value), path="$.state_value")
        object.__setattr__(self, "state_value", FrozenDict(frozen))
        if not isinstance(self.provenance, FrozenDict):
            object.__setattr__(self, "provenance", FrozenDict(dict(self.provenance)))


@dataclass(frozen=True, slots=True)
class KernelWorkingRealization:
    """One realized node with a generic state key."""

    structure: Any
    state_key: ComponentStateKey
    parent_realization_id: str | None = None
    generation_axis: str | None = None
    locked_axes: tuple[str, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        from confflow.domain.structure import StructureRecord

        if not isinstance(self.structure, StructureRecord):
            raise ValueError("structure must be a StructureRecord")
        if not isinstance(self.state_key, ComponentStateKey):
            raise ValueError("state_key must be a ComponentStateKey")
        if self.generation_axis is not None and (
            not isinstance(self.generation_axis, str) or not self.generation_axis
        ):
            raise ValueError("generation_axis must be a non-empty string")
        object.__setattr__(self, "locked_axes", tuple(self.locked_axes))
        for axis in self.locked_axes:
            if not isinstance(axis, str) or not axis:
                raise ValueError(f"locked axis {axis!r} must be a non-empty string")
        if not isinstance(self.provenance, FrozenDict):
            object.__setattr__(self, "provenance", FrozenDict(dict(self.provenance)))


@dataclass(frozen=True, slots=True)
class KernelRun:
    """Generic engine run: leaves plus audit records (no v3 projection)."""

    leaves: tuple[KernelWorkingRealization, ...] = ()
    target_records: tuple[Any, ...] = ()
    report: Mapping[str, Any] = field(default_factory=FrozenDict)
    certificate: Any = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "leaves", tuple(self.leaves))
        object.__setattr__(self, "target_records", tuple(self.target_records))
        if not isinstance(self.report, FrozenDict):
            object.__setattr__(self, "report", FrozenDict(dict(self.report)))

    def report_json(self) -> dict[str, Any]:
        """Return the report as plain JSON-compatible data."""
        report = self.report
        if isinstance(report, FrozenDict):
            return report.thaw()
        return dict(report)


def as_kernel_target(t: Any) -> KernelGenerationTarget:
    """Convert an old or generic target into a :class:`KernelGenerationTarget`.

    Only definition/export site (G13). Reads fields only, never touches wire.
    """
    if isinstance(t, KernelGenerationTarget):
        return t
    return KernelGenerationTarget(
        axis=str(t.axis),
        target_id=str(t.target_id),
        state_value=dict(t.state_value),
        ordinal=int(t.ordinal),
        provenance=dict(t.provenance),
    )


@dataclass(slots=True)
class TopologyBuildContext:
    """Mutable typed-graph build authority (FIX-1A A4c, chemistry-agnostic).

    Field names/types mirror ``_build_typed_graph`` locals exactly (PLAN A4c
    V52 note): ``n_atoms: int``, ``adjacency: list[set[int]]``,
    ``typed: dict[tuple[int, int, EdgeType], TypedEdge]`` (3-tuple key with
    kind, NOT the PLAN draft ``edges: dict[tuple[int, int], ...]``),
    ``explicit_covalent: set[tuple[int, int]]``, ``check_index`` wrapping
    the local ``_check(value, path)``. Holds references (same objects, not
    copies) so ``contribute_topology`` mutations are authoritative and edge
    order/digest stay byte-identical.
    """

    n_atoms: int
    adjacency: list[set[int]]
    typed: dict[tuple[int, int, Any], Any]
    explicit_covalent: set[tuple[int, int]]
    check_index: Callable[[int, str], None]
