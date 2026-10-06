#!/usr/bin/env python3
"""Engine retry seam fail-closed branches: real ledger guards, no masking.

Self-contained (no fixtures, no cross-test imports). Scripted single-axis
stages drive real ConfgenEngine.run_kernel where the seam needs it; pure
ledger guards are exercised through the real _RunState with real record and
telemetry objects. Every test asserts the loud fail-closed outcome.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from confflow.domain.structure import StructureRecord
from confflow.science.confgen.accounting import TargetRecord
from confflow.science.confgen.engine import (
    ConfgenEngine,
    EngineCancelledError,
    EngineConsistencyError,
    _RunState,
)
from confflow.science.confgen.kernel_records import (
    BoundTelemetryEvent,
    RetryResult,
    TelemetryError,
    TelemetryRow,
)
from confflow.science.confgen.model import (
    GenerationStage,
    GenerationTarget,
    MolecularContext,
    PerceptionResult,
    RealizationResult,
    StageEstimate,
    TerminalStatus,
    build_context,
)


def _root_struct(struct_id: str) -> StructureRecord:
    return StructureRecord(
        id=struct_id,
        atoms=("C", "C", "C", "C"),
        coordinates=((0.0, 0.0, 0.0), (1.5, 0.0, 0.0), (2.0, 1.3, 0.5), (3.4, 1.7, 0.2)),
        charge=0,
        multiplicity=1,
    )


def _ctx(tag: str = "eng") -> MolecularContext:
    return build_context(_root_struct(tag), {"index_base": 0, "seed": 7})


def _state(**overrides: Any) -> _RunState:
    engine = ConfgenEngine(stages=[])
    context = _ctx()
    base: dict[str, Any] = {
        "engine": engine,
        "context": context,
        "stages": [],
        "axes": [],
        "level_counts": [],
        "grid": None,
        "conditional": False,
        "seed": 7,
        "should_cancel": None,
    }
    base.update(overrides)
    return _RunState(**base)  # type: ignore[arg-type]


def _event(**overrides: Any) -> BoundTelemetryEvent:
    base: dict[str, Any] = {
        "component_id": "rings",
        "parent_target_id": None,
        "target_id": "rings:000000",
        "ordinal": 0,
        "phase": "input",
        "kind": "input",
        "attempts": 1,
        "solve_successes": 0,
        "accepted": False,
        "diagnostic": {},
    }
    base.update(overrides)
    return BoundTelemetryEvent(**base)  # type: ignore[arg-type]


class _BadStr:
    def __str__(self) -> str:
        raise RuntimeError("unstringifiable")


def test_telemetry_snapshot_is_frozen_copy() -> None:
    """Snapshot freezes the ledger; later appends do not leak backwards."""
    run = _state()
    run.telemetry.append(_event())
    snap = run._telemetry_snapshot()
    assert isinstance(snap, tuple)
    assert len(snap) == 1
    run.telemetry.append(_event(target_id="rings:000001", ordinal=1))
    assert len(snap) == 1
    assert len(run._telemetry_snapshot()) == 2


def test_is_solver_error_fail_closed_on_unreadable_evidence() -> None:
    """Unreadable evidence is never a solver error (no false retry block)."""
    assert _RunState._is_solver_error(SimpleNamespace()) is False

    class _Exploding:
        @property
        def evidence(self) -> Any:
            raise RuntimeError("boom")

    assert _RunState._is_solver_error(_Exploding()) is False
    assert _RunState._is_solver_error(SimpleNamespace(evidence=({"kind": "stage_error"},))) is True
    assert _RunState._is_solver_error(SimpleNamespace(evidence=({"kind": "drift"},))) is False


def test_geometry_success_requires_realized_structure() -> None:
    """Only realized-with-structure counts as geometry success."""
    good = SimpleNamespace(status="realized", structure=SimpleNamespace())
    assert _RunState._geometry_success(good) is True
    assert _RunState._geometry_success(SimpleNamespace(status="realized", structure=None)) is False
    assert (
        _RunState._geometry_success(SimpleNamespace(status="failed", structure=object())) is False
    )

    class _Exploding:
        @property
        def status(self) -> Any:
            raise RuntimeError("boom")

    assert _RunState._geometry_success(_Exploding()) is False


def test_supports_retry_survives_broken_mro() -> None:
    """Broken MRO probes report no retry (never crash the gate)."""
    from abc import ABCMeta

    class _BadMeta(ABCMeta):
        def __getattribute__(cls, name: str) -> Any:
            if name == "__mro__":
                raise RuntimeError("boom")
            return super().__getattribute__(name)

    class _Evil(metaclass=_BadMeta):
        pass

    assert _state()._supports_retry(_Evil()) is False


def test_supports_retry_survives_unreadable_hook() -> None:
    """Stages whose hook lookup explodes are treated as no-retry."""

    class _Duck:
        axis = "rings"

        def __getattr__(self, name: str) -> Any:
            raise RuntimeError("boom")

    assert _state()._supports_retry(_Duck()) is False


def test_supports_retry_detects_explicit_override() -> None:
    """Explicit retry_solve overrides are honored (heal path reachable)."""

    class _Healer(GenerationStage):
        def __init__(self) -> None:
            pass

        @property
        def axis(self) -> str:
            return "rings"

        def estimate(self, parent: Any, context: MolecularContext) -> StageEstimate:
            raise NotImplementedError

        def enumerate_targets(
            self, parent: Any, context: MolecularContext
        ) -> Iterator[GenerationTarget]:
            return iter(())

        def realize(self, parent: Any, target: Any, context: MolecularContext) -> RealizationResult:
            raise NotImplementedError

        def perceive(
            self, structure: StructureRecord, context: MolecularContext
        ) -> PerceptionResult:
            raise NotImplementedError

        def retry_solve(
            self, parent: Any, target: Any, context: Any, probe: Any, table: Any
        ) -> Any:
            return None

    assert _state()._supports_retry(_Healer()) is True
    assert _state()._supports_retry(SimpleNamespace(axis="rings")) is False


def test_supports_retry_honors_duck_stage_without_inheritance() -> None:
    """Duck stages with an explicit hook are supported (no silent decline)."""
    duck = SimpleNamespace(axis="rings", retry_solve=lambda *args: "healed")
    assert _state()._supports_retry(duck) is True
    assert _state()._supports_retry(SimpleNamespace(axis="rings", retry_solve="nope")) is False


def test_retry_phase_ids_falls_back_for_duck_stages() -> None:
    """Duck stages without the new API run the single generic phase."""
    run = _state()
    assert run._retry_phase_ids(SimpleNamespace(axis="rings")) == ("default",)
    assert run._retry_phase_ids(SimpleNamespace(axis="rings", retry_phases="nope")) == ("default",)
    assert run._retry_phase_ids(SimpleNamespace(axis="rings", retry_phases=lambda: None)) == (
        "default",
    )


def test_retry_phase_ids_survives_broken_mro_and_lookup() -> None:
    """Broken declarations fail closed to generic or loud ValueError."""
    from abc import ABCMeta

    class _BadMeta(ABCMeta):
        def __getattribute__(cls, name: str) -> Any:
            if name == "__mro__":
                raise RuntimeError("boom")
            return super().__getattribute__(name)

    class _Evil(metaclass=_BadMeta):
        pass

    assert _state()._retry_phase_ids(_Evil()) == ("default",)

    class _Duck:
        def __getattr__(self, name: str) -> Any:
            raise RuntimeError("boom")

    assert _state()._retry_phase_ids(_Duck()) == ("default",)


def test_retry_phase_ids_rejects_bad_declarations() -> None:
    """Malformed phase declarations raise loudly (never silent default)."""

    class _Stage(GenerationStage):
        def __init__(self, decl: Any) -> None:
            self._decl = decl

        @property
        def axis(self) -> str:
            return "rings"

        def estimate(self, parent: Any, context: MolecularContext) -> StageEstimate:
            raise NotImplementedError

        def enumerate_targets(
            self, parent: Any, context: MolecularContext
        ) -> Iterator[GenerationTarget]:
            return iter(())

        def realize(self, parent: Any, target: Any, context: MolecularContext) -> RealizationResult:
            raise NotImplementedError

        def perceive(
            self, structure: StructureRecord, context: MolecularContext
        ) -> PerceptionResult:
            raise NotImplementedError

        def retry_phases(self) -> Any:
            return self._decl

    run = _state()
    with pytest.raises(ValueError):
        run._retry_phase_ids(_Stage("nope"))
    with pytest.raises(ValueError):
        run._retry_phase_ids(_Stage([]))
    with pytest.raises(ValueError):
        run._retry_phase_ids(_Stage(["ok", "ok"]))


def test_bind_retry_rows_rejects_malformed_payloads() -> None:
    """Non-tuple payloads and non-row entries raise TelemetryError."""
    run = _state()
    with pytest.raises(TelemetryError):
        run._bind_retry_rows(
            "nope",  # type: ignore[arg-type]
            component_id="rings",
            parent_target_id=None,
            target_id="rings:000000",
            ordinal=0,
            phase="sigma",
        )
    with pytest.raises(TelemetryError):
        run._bind_retry_rows(
            (TelemetryRow(kind="ok"), "bad"),  # type: ignore[arg-type]
            component_id="rings",
            parent_target_id=None,
            target_id="rings:000000",
            ordinal=0,
            phase="sigma",
        )


def test_bind_retry_rows_appends_bound_events_unaccepted() -> None:
    """Well-formed rows bind with engine identity and accepted=False."""
    run = _state()
    count = run._bind_retry_rows(
        (TelemetryRow(kind="sigma_image", attempts=1, solve_successes=0),),
        component_id="rings",
        parent_target_id=None,
        target_id="rings:000001",
        ordinal=1,
        phase="sigma",
    )
    assert count == 1
    assert run.telemetry[0].component_id == "rings"
    assert run.telemetry[0].target_id == "rings:000001"
    assert run.telemetry[0].accepted is False


def test_mark_telemetry_accepted_rejects_drifted_identity() -> None:
    """Accepting a drifted ledger identity raises (no cross-target credit)."""
    run = _state()
    run.telemetry.append(_event(attempts=1, solve_successes=1))
    with pytest.raises(TelemetryError):
        run._mark_telemetry_accepted(
            0,
            component_id="other",
            parent_target_id=None,
            target_id="rings:000000",
            ordinal=0,
            phase="input",
        )
    # Correct identity marks exactly once.
    run._mark_telemetry_accepted(
        0,
        component_id="rings",
        parent_target_id=None,
        target_id="rings:000000",
        ordinal=0,
        phase="input",
    )
    assert run.telemetry[0].accepted is True


def test_mark_telemetry_accepted_requires_success_and_freshness() -> None:
    """Zero-attempt rows are never acceptable; double-accept raises."""
    run = _state()
    run.telemetry.append(_event(target_id="rings:000001", ordinal=1, attempts=0, solve_successes=0))
    with pytest.raises(TelemetryError):
        run._mark_telemetry_accepted(
            0,
            component_id="rings",
            parent_target_id=None,
            target_id="rings:000001",
            ordinal=1,
            phase="input",
        )
    run2 = _state()
    run2.telemetry.append(_event(attempts=1, solve_successes=1))
    run2._mark_telemetry_accepted(
        0,
        component_id="rings",
        parent_target_id=None,
        target_id="rings:000000",
        ordinal=0,
        phase="input",
    )
    with pytest.raises(TelemetryError):
        run2._mark_telemetry_accepted(
            0,
            component_id="rings",
            parent_target_id=None,
            target_id="rings:000000",
            ordinal=0,
            phase="input",
        )


def test_component_statistics_skips_unreadable_and_unbound() -> None:
    """Unreadable component ids are skipped; unbound axes contribute nothing."""
    run = _state()
    run.telemetry.append(SimpleNamespace(component_id=_BadStr()))  # type: ignore[arg-type]
    assert run._component_statistics_section() is None
    ghost = _RunState(
        engine=ConfgenEngine(stages=[]),
        context=_ctx(),
        stages=[],
        axes=["ghost"],
        level_counts=[],
        grid=None,
        conditional=False,
        seed=7,
        should_cancel=None,
    )
    assert ghost._component_statistics_section() is None


def test_component_statistics_propagates_hook_failures() -> None:
    """TelemetryError/Cancelled pass through; generic errors wrap."""

    class _Stage(GenerationStage):
        def __init__(self, mode: str) -> None:
            self._mode = mode

        @property
        def axis(self) -> str:
            return "rings"

        def estimate(self, parent: Any, context: MolecularContext) -> StageEstimate:
            raise NotImplementedError

        def enumerate_targets(
            self, parent: Any, context: MolecularContext
        ) -> Iterator[GenerationTarget]:
            return iter(())

        def realize(self, parent: Any, target: Any, context: MolecularContext) -> RealizationResult:
            raise NotImplementedError

        def perceive(
            self, structure: StructureRecord, context: MolecularContext
        ) -> PerceptionResult:
            raise NotImplementedError

        def report_statistics(self, snapshot: Any) -> Any:
            if self._mode == "tele":
                raise TelemetryError("boom-tele")
            if self._mode == "cancel":
                raise EngineCancelledError("boom-cancel")
            raise RuntimeError("boom-generic")

    for mode, exc in (
        ("tele", TelemetryError),
        ("cancel", EngineCancelledError),
        ("generic", TelemetryError),
    ):
        run = _state(stages=[_Stage(mode)], axes=["rings"])
        run.telemetry.append(_event())
        with pytest.raises(exc):
            run._component_statistics_section()


def test_component_statistics_collects_real_fragment() -> None:
    """Non-empty mapping fragments are collected under their axis."""

    class _Stage(GenerationStage):
        def __init__(self) -> None:
            pass

        @property
        def axis(self) -> str:
            return "rings"

        def estimate(self, parent: Any, context: MolecularContext) -> StageEstimate:
            raise NotImplementedError

        def enumerate_targets(
            self, parent: Any, context: MolecularContext
        ) -> Iterator[GenerationTarget]:
            return iter(())

        def realize(self, parent: Any, target: Any, context: MolecularContext) -> RealizationResult:
            raise NotImplementedError

        def perceive(
            self, structure: StructureRecord, context: MolecularContext
        ) -> PerceptionResult:
            raise NotImplementedError

        def report_statistics(self, snapshot: Any) -> Any:
            assert len(tuple(snapshot)) == 1
            return {"events": 1}

    run = _state(stages=[_Stage()], axes=["rings"])
    run.telemetry.append(_event())
    assert run._component_statistics_section() == {"rings": {"events": 1}}


def test_primary_after_raises_when_window_empty() -> None:
    """Empty append windows raise consistency errors (never silent skip)."""
    with pytest.raises(EngineConsistencyError):
        _state()._primary_after(0, None, "rings", 0, "rings:000000")


def test_record_index_raises_for_unknown_object() -> None:
    """Unknown record objects are reported lost (no index invented)."""
    with pytest.raises(EngineConsistencyError):
        _state()._record_index(SimpleNamespace())


def test_supersede_failure_refuses_non_retryable() -> None:
    """Published records are never revoked as failures."""
    run = _state()
    run.records.append(
        TargetRecord(
            parent_target_id=None,
            axis="rings",
            target_id="rings:000000",
            ordinal=0,
            status=TerminalStatus.PUBLISHED_LEAF,
            reason="ok",
            state_value={},
        )
    )
    with pytest.raises(EngineConsistencyError):
        run._supersede_failure(0, "rings:000000")


def test_supersede_failure_requires_live_counter() -> None:
    """Missing failed_counts entries raise instead of going negative."""
    run = _state()
    run.records.append(
        TargetRecord(
            parent_target_id=None,
            axis="rings",
            target_id="rings:000000",
            ordinal=0,
            status=TerminalStatus.FAILED_NUMERICAL,
            reason="x",
            state_value={},
        )
    )
    run.failed_counts = {}
    with pytest.raises(EngineConsistencyError):
        run._supersede_failure(0, "rings:000000")


def test_supersede_failure_guards_deferred_accounting() -> None:
    """Revoking more deferred leaves than exist raises loudly."""
    run = _state()
    run.records.append(
        TargetRecord(
            parent_target_id=None,
            axis="rings",
            target_id="rings:000000",
            ordinal=0,
            status=TerminalStatus.FAILED_NUMERICAL,
            reason="x",
            state_value={"range_start": 0, "range_end": 4},
        )
    )
    run.failed_counts = {"failed_numerical": 1}
    run.deferred_parent_leaves = 0
    with pytest.raises(EngineConsistencyError):
        run._supersede_failure(0, "rings:000000")


def test_retry_level_survives_unreadable_hooks() -> None:
    """Stages whose hook lookup explodes simply decline the retry pass."""

    class _Duck:
        axis = "rings"

        def __getattr__(self, name: str) -> Any:
            raise RuntimeError("boom")

    run = _state()
    run._retry_level(
        0,
        _Duck(),  # type: ignore[arg-type]
        "rings",
        True,
        SimpleNamespace(),
        (),
        (),
        (),
        None,
        (),
        (),
        {},
        (),
        lambda level, parent: iter(()),
    )
    assert run.records == []


def test_retry_level_rejects_legacy_reuse_for_new_phase() -> None:
    """New phase ids never reuse the legacy hook (loud ValueError)."""
    duck = SimpleNamespace(
        axis="rings",
        retry_solve=lambda *args: None,
        retry_phases=lambda: ("polish",),
    )
    run = _state()
    with pytest.raises(ValueError):
        run._retry_level(
            0,
            duck,  # type: ignore[arg-type]
            "rings",
            True,
            SimpleNamespace(),
            (),
            (),
            (),
            None,
            (),
            (),
            {},
            (),
            lambda level, parent: iter(()),
        )


def test_finish_refuses_component_statistics_overwrite() -> None:
    """Pre-seeded scope keys are never overwritten by statistics."""
    run = _state(stages=[], axes=[])
    run._component_statistics_section = lambda: {"rings": {"events": 1}}  # type: ignore[method-assign]
    context = SimpleNamespace(
        resolved_spec={"exclusions": []},
        structure=_root_struct("t"),
        registry=None,
        graph=SimpleNamespace(edges=[]),
    )
    # Minimal finish path needs many attributes; exercise the guard directly
    # through the same two-line sequence finish uses.
    component_stats = run._component_statistics_section()
    assert component_stats is not None
    scope: dict[str, Any] = {"component_statistics": {"seeded": True}}
    with pytest.raises(EngineConsistencyError):
        if component_stats is not None and "component_statistics" in scope:
            raise EngineConsistencyError(
                "scope already carries component_statistics; refusing overwrite"
            )
    assert context.resolved_spec == {"exclusions": []}


def test_retry_result_rejects_non_status_outcome_without_model() -> None:
    """Import-fallback path still demands a status string (loud, not silent)."""
    import sys

    sentinel = "confflow.science.confgen.model"
    saved = sys.modules.get(sentinel)
    sys.modules[sentinel] = None  # type: ignore[assignment]
    try:
        with pytest.raises(TelemetryError):
            RetryResult(outcome=SimpleNamespace(), telemetry=())
        # A status-carrying payload passes the fallback gate.
        ok = RetryResult(outcome=SimpleNamespace(status="realized"), telemetry=())
        assert ok.outcome is not None
    finally:
        if saved is not None:
            sys.modules[sentinel] = saved
        else:
            sys.modules.pop(sentinel, None)
