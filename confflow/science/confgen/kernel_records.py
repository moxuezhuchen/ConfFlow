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
    "BoundTelemetryEvent",
    "ComponentInheritedState",
    "ComponentStateKey",
    "InheritedScopeError",
    "KernelGenerationTarget",
    "KernelRun",
    "KernelWorkingRealization",
    "RetryResult",
    "TelemetryError",
    "TelemetryRow",
    "TopologyBuildContext",
    "VerificationResult",
    "as_kernel_target",
]


class TelemetryError(ValueError):
    """Generic fail-closed error for malformed telemetry rows.

    Distinct ``ValueError`` subclass so the engine can re-raise it
    explicitly instead of swallowing it as ``stage_error``. Never
    relaxes science judgements; terminal records keep their existing
    semantics.
    """


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


@dataclass(frozen=True, slots=True)
class ComponentInheritedState:
    """Generic opaque inherited payload for one component (FIX-1A A4d).

    The kernel never interprets ``payload``; the owning component's
    ``verify_inherited_state`` does. ``payload`` must be JSON-freezable
    (plain mappings/lists/str/num/bool/None); dataclass instances or
    other non-JSON objects fail closed via :func:`_freeze_json` (no fake
    JSON serialization). Internal lock objects (e.g. torsion locks)
    stay inside the component; only the public wire payload travels here.
    """

    component_id: str
    payload: Any = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        if not isinstance(self.component_id, str) or not self.component_id:
            raise ValueError("component_id must be a non-empty string")
        frozen = _freeze_json(
            dict(self.payload) if isinstance(self.payload, Mapping) else self.payload,
            path="$.payload",
        )
        if isinstance(frozen, dict):
            object.__setattr__(self, "payload", FrozenDict(frozen))
        elif isinstance(frozen, list):
            object.__setattr__(self, "payload", tuple(frozen))
        else:
            object.__setattr__(self, "payload", frozen)


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Minimal generic inherited-check result (FIX-1A A4d).

    Mirrors the existing ``check_inherited_torsion_locks`` return
    semantics ``(ok, evidence)`` without inventing thresholds or new
    conditions. ``ok`` is True when the carried state still holds on
    the given geometry; ``evidence`` holds drift dicts (empty when ok).
    Scope failures raise :class:`InheritedScopeError` instead of
    returning ``ok=False`` (verbatim legacy behavior).
    """

    ok: bool
    evidence: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.ok, bool):
            raise ValueError("ok must be a bool")
        object.__setattr__(self, "evidence", tuple(self.evidence))


@dataclass(frozen=True, slots=True)
class TelemetryRow:
    """Component-owned generic telemetry row (L-D3 logic seam).

    Opaque component vocabulary: ``kind`` is a component-owned label
    (kernel never interprets it and never aggregates axis names);
    ``attempts`` counts attempted starts represented by this row;
    ``solve_successes`` counts geometry successes
    (``outcome.status == "realized"`` with a structure) among them;
    ``diagnostic`` is immutable JSON-compatible component data.
    Identity (component/parent/target/phase) is bound by the engine,
    never trusted from the caller.
    """

    kind: str
    attempts: int = 1
    solve_successes: int = 0
    diagnostic: Mapping[str, Any] = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind:
            raise TelemetryError("telemetry kind must be a non-empty string")
        for name in ("attempts", "solve_successes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TelemetryError(f"telemetry {name} must be an integer >= 0")
        if int(self.solve_successes) > int(self.attempts):
            raise TelemetryError("telemetry solve_successes must not exceed attempts")
        try:
            frozen = _freeze_json(dict(self.diagnostic), path="$.diagnostic")
        except ValueError as exc:
            raise TelemetryError(str(exc)) from exc
        object.__setattr__(self, "diagnostic", FrozenDict(frozen))


@dataclass(frozen=True, slots=True)
class BoundTelemetryEvent:
    """Engine-bound telemetry event (L-D3 logic seam).

    ``component_id``/``parent_target_id``/``target_id``/``ordinal``/
    ``phase`` are bound by the engine at the real solve gate; the
    kernel never interprets ``kind``. ``solve_successes`` is the
    geometry-success count (outcome realized with structure);
    ``accepted`` is the engine-audited publication flag and lives in
    a separate column (never merged into success). Immutable.
    """

    component_id: str
    parent_target_id: str | None
    target_id: str
    ordinal: int
    phase: str
    kind: str
    attempts: int = 1
    solve_successes: int = 0
    accepted: bool = False
    diagnostic: Mapping[str, Any] = field(default_factory=FrozenDict)

    def __post_init__(self) -> None:
        if not isinstance(self.component_id, str) or not self.component_id:
            raise TelemetryError("telemetry component_id must be a non-empty string")
        if self.parent_target_id is not None and (
            not isinstance(self.parent_target_id, str) or not self.parent_target_id
        ):
            raise TelemetryError("telemetry parent_target_id must be None or a non-empty string")
        if not isinstance(self.target_id, str) or not self.target_id:
            raise TelemetryError("telemetry target_id must be a non-empty string")
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 0:
            raise TelemetryError("telemetry ordinal must be an integer >= 0")
        if not isinstance(self.phase, str) or not self.phase:
            raise TelemetryError("telemetry phase must be a non-empty string")
        if not isinstance(self.kind, str) or not self.kind:
            raise TelemetryError("telemetry kind must be a non-empty string")
        for name in ("attempts", "solve_successes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TelemetryError(f"telemetry {name} must be an integer >= 0")
        if int(self.solve_successes) > int(self.attempts):
            raise TelemetryError("telemetry solve_successes must not exceed attempts")
        if not isinstance(self.accepted, bool):
            raise TelemetryError("telemetry accepted must be a bool")
        try:
            frozen = _freeze_json(dict(self.diagnostic), path="$.diagnostic")
        except ValueError as exc:
            raise TelemetryError(str(exc)) from exc
        object.__setattr__(self, "diagnostic", FrozenDict(frozen))


@dataclass(frozen=True, slots=True)
class RetryResult:
    """Explicit frozen wrapper for phase-aware retry outcomes (L-D3).

    Only this exact type is unwrapped by the engine; any other tuple
    keeps the legacy plain-outcome semantics. ``outcome`` is the
    optional solver outcome (``None`` declines the retry but keeps
    ``telemetry``); ``telemetry`` is the frozen tuple of
    component-owned :class:`TelemetryRow` entries (one per attempted
    start, including failures). ``success_index`` optionally names the
    position in ``telemetry`` of the successful start; when set it must
    point at a row with ``attempts > 0`` and ``solve_successes > 0``
    (a trailing zero-attempt diagnostic row is never selectable).
    Without it the engine requires exactly one successful row
    (unique-success rule, ambiguous payloads rejected loudly).
    Malformed telemetry raises :class:`TelemetryError` visibly (never
    ``stage_error``).
    """

    outcome: Any = None
    telemetry: tuple[TelemetryRow, ...] = ()
    success_index: int | None = None

    def __post_init__(self) -> None:
        outcome = self.outcome
        if outcome is not None:
            try:
                from confflow.science.confgen.model import RealizationResult as _Outcome
            except Exception:
                _Outcome = None  # type: ignore[assignment]
            if _Outcome is not None and not isinstance(outcome, _Outcome):
                raise TelemetryError(
                    "RetryResult outcome must be RealizationResult or None, "
                    f"got {type(outcome).__name__}"
                )
            if _Outcome is None:
                status = getattr(outcome, "status", None)
                if not isinstance(status, str):
                    raise TelemetryError("RetryResult outcome must carry a status string")
        rows = self.telemetry
        if not isinstance(rows, (tuple, list)):
            raise TelemetryError("RetryResult telemetry must be a tuple of TelemetryRow")
        frozen_rows: list[TelemetryRow] = []
        for entry in list(rows):
            if not isinstance(entry, TelemetryRow):
                raise TelemetryError(
                    "RetryResult telemetry entries must be TelemetryRow, "
                    f"got {type(entry).__name__}"
                )
            frozen_rows.append(entry)
        object.__setattr__(self, "telemetry", tuple(frozen_rows))
        index = self.success_index
        if index is None:
            return
        if isinstance(index, bool) or not isinstance(index, int):
            raise TelemetryError("RetryResult success_index must be an int or None")
        if index < 0 or index >= len(frozen_rows):
            raise TelemetryError(
                f"RetryResult success_index {index} out of range "
                f"for {len(frozen_rows)} telemetry rows"
            )
        if outcome is None:
            raise TelemetryError("RetryResult success_index requires a successful outcome")
        chosen = frozen_rows[index]
        if int(chosen.attempts) <= 0 or int(chosen.solve_successes) <= 0:
            raise TelemetryError(
                "RetryResult success_index must point at a row with "
                "attempts > 0 and solve_successes > 0, "
                f"got attempts={int(chosen.attempts)} "
                f"solve_successes={int(chosen.solve_successes)}"
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
