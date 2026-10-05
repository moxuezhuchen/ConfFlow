#!/usr/bin/env python3
"""A6 DummyComponent (test-only, proves kernel needs no change for a new axis).

A declared atom is translated by +/-0.1 Ang along x, giving 2 states.
``spec_keys=("dummy",)``, ``order=40``. All hooks are owned here; the kernel
only drives them through the registry. Parent-relative lock
(``lock_reference="parent"``) with a real re-measurement in
``verify_locked`` so a downstream tamper is caught as drift by the engine.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from confflow.domain._immutable import FrozenDict
from confflow.science.confgen.kernel_records import VerificationResult
from confflow.science.confgen.registry import ComponentDescriptor

__all__ = ["DISPLACEMENT", "descriptor", "dummy_descriptor"]

DISPLACEMENT = 0.1
_TOLERANCE = 0.02


def normalize_spec(raw: Mapping[str, Any], *, index_base: int) -> Mapping[str, Any]:
    """Return only owned ``dummy`` keys (1-based atom -> 0-based)."""
    if "dummy" not in raw:
        return {}
    entries = raw["dummy"]
    if not isinstance(entries, (list, tuple)):
        raise ValueError("dummy section must be a list")
    out: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, Mapping):
            raise ValueError("dummy entry must be a mapping")
        if "atom" not in item:
            raise ValueError("dummy entry missing 'atom'")
        atom = int(item["atom"])
        if index_base == 1:
            atom -= 1
        elif index_base != 0:
            raise ValueError(f"unknown index_base {index_base!r}")
        if atom < 0:
            raise ValueError(f"dummy atom index out of range: {atom}")
        disp = float(item.get("displacement", DISPLACEMENT))
        if abs(disp - DISPLACEMENT) > 1e-12:
            raise ValueError("dummy displacement must be 0.1 Ang")
        out.append(
            {
                "atom": atom,
                "displacement": disp,
                "id": str(item.get("id", f"a{atom}")),
            }
        )
    return {"dummy": out}


def validate_context(resolved: Mapping[str, Any], context: Any) -> None:
    """Range-check the declared atom against the built context."""
    entries = resolved.get("dummy", []) or []
    n_atoms = len(context.structure.atoms)
    for entry in entries:
        atom = int(entry["atom"])
        if not 0 <= atom < n_atoms:
            raise ValueError(f"dummy atom {atom} out of range for {n_atoms} atoms")


def serialize_inherited_state(resolved: Mapping[str, Any], state_value: Any, context: Any) -> Any:
    """Echo the dummy state slice (never reads ``context.input_state_key``)."""
    return {"echo": dict(state_value or {})}


def verify_inherited_state(structure: Any, state_value: Any, payload: Any, context: Any) -> Any:
    """Accept the carried dummy slice (geometry is audited by parent locks)."""
    assert state_value is not None
    return VerificationResult(ok=True, evidence=())


class DummyStage:
    """Two-state (+/-0.1 Ang) translation stage for the declared atom."""

    axis = "dummy"
    lock_reference = "parent"

    def __init__(self, resolved: Mapping[str, Any]) -> None:
        entries = list(resolved.get("dummy", []) or [])
        if not entries:
            raise ValueError("dummy stage requires non-empty dummy section")
        self._entry = dict(entries[0])

    def is_conditional(self, context: Any) -> bool:
        return False

    def axis_ids(self, context: Any) -> Any:
        return None

    def estimate(self, parent: Any, context: Any) -> Any:
        from confflow.science.confgen.model import StageEstimate

        return StageEstimate(
            declared_count=2,
            upper_bound=2,
            exact=True,
            details=FrozenDict({"basis": "dummy", "scope_coverage": "exact"}),
        )

    def enumerate_targets(self, parent: Any, context: Any) -> Any:
        from confflow.science.confgen.kernel_records import KernelGenerationTarget

        atom = int(self._entry["atom"])
        disp = float(self._entry["displacement"])
        for ordinal, sign in ((0, 1.0), (1, -1.0)):
            yield KernelGenerationTarget(
                axis="dummy",
                target_id=f"dummy:{ordinal:06d}",
                state_value={
                    "atom": atom,
                    "displacement": disp,
                    "direction": "+" if sign > 0 else "-",
                    "shift": sign * disp,
                },
                ordinal=ordinal,
                provenance=FrozenDict({}),
            )

    def realize(self, parent: Any, target: Any, context: Any) -> Any:
        from confflow.domain.structure import StructureRecord
        from confflow.science.confgen.model import RealizationResult

        state = dict(target.state_value)
        atom = int(state["atom"])
        shift = float(state["shift"])
        base = parent.structure
        coords = [list(map(float, row)) for row in base.coordinates]
        coords[atom][0] += shift
        struct = StructureRecord(
            id=f"{base.id}:d{target.ordinal}",
            atoms=tuple(base.atoms),
            coordinates=tuple(tuple(row) for row in coords),
            charge=int(base.charge),
            multiplicity=int(base.multiplicity),
        )
        return RealizationResult(
            structure=struct, status="realized", reason="dummy", backend="dummy"
        )

    def perceive(self, structure: Any, context: Any) -> Any:
        from confflow.science.confgen.model import PerceptionResult

        atom = int(self._entry["atom"])
        disp = float(self._entry["displacement"])
        ref = context.input_coords[atom][0]
        got = float(structure.coordinates[atom][0])
        delta = got - ref
        if abs(delta - disp) < _TOLERANCE:
            direction = "+"
        elif abs(delta + disp) < _TOLERANCE:
            direction = "-"
        else:
            return PerceptionResult(
                best_key={"atom": atom, "direction": "unknown", "shift": delta},
                confidence="ambiguous",
            )
        return PerceptionResult(
            best_key={
                "atom": atom,
                "displacement": disp,
                "direction": direction,
                "shift": disp if direction == "+" else -disp,
            },
            confidence="reported",
        )

    def verify_locked(
        self, structure: Any, locked_state: Mapping[str, Any], context: Any
    ) -> tuple[bool, dict[str, Any], list[dict[str, Any]]]:
        """Re-measure the shift against the parent geometry (drift on tamper)."""
        atom = int(locked_state["atom"])
        expected = float(locked_state["shift"])
        ref = context.input_coords[atom][0]
        got = float(structure.coordinates[atom][0])
        measured = got - ref
        if abs(measured - expected) <= _TOLERANCE:
            return True, {"atom": atom, "shift": measured}, []
        return (
            False,
            {"atom": atom, "shift": measured},
            [
                {
                    "kind": "drift",
                    "axis": "dummy",
                    "detail": (
                        f"dummy lock drift: expected {expected:+.3f} " f"measured {measured:+.3f}"
                    ),
                    "expected": expected,
                    "measured": measured,
                    "tolerance": _TOLERANCE,
                }
            ],
        )


def descriptor() -> ComponentDescriptor:
    """Return the dummy component descriptor (order 40, owns ``dummy``)."""

    def _is_active(resolved: Mapping[str, Any]) -> bool:
        return bool(resolved.get("dummy"))

    def _factory(resolved: Mapping[str, Any]) -> DummyStage:
        return DummyStage(resolved)

    return ComponentDescriptor(
        id="dummy",
        order=40,
        spec_keys=("dummy",),
        state_merge="merge",
        is_active=_is_active,
        factory=_factory,
        normalize_spec=normalize_spec,
        validate_context=validate_context,
        serialize_inherited_state=serialize_inherited_state,
        verify_inherited_state=verify_inherited_state,
    )


dummy_descriptor = descriptor
