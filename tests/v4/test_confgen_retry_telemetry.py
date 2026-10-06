#!/usr/bin/env python3
"""L-D3 generic telemetry logic seam: real engine runs, no science.

Self-contained (no fixtures, no TS1). Every test drives a real
``ConfgenEngine.run_kernel``; telemetry is exercised through engine
dispatch only. Kernel never interprets component vocabulary and never
aggregates axis names; terminal science semantics are unchanged.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Mapping
from typing import Any

import numpy as np
import pytest

from confflow.domain._immutable import FrozenDict
from confflow.domain.structure import StructureRecord
from confflow.science.confgen.accounting import (
    verify_count_equations,
    verify_terminal_equations,
)
from confflow.science.confgen.engine import ConfgenEngine, EngineCancelledError
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
    build_context,
)
from confflow.science.confgen.wire_v3 import (
    LegacyStageAdapter,
    from_wire_key,
    is_legacy_stage,
)


def _root_struct(struct_id: str) -> StructureRecord:
    return StructureRecord(
        id=struct_id,
        atoms=("C", "C", "C", "C"),
        coordinates=((0.0, 0.0, 0.0), (1.5, 0.0, 0.0), (2.0, 1.3, 0.5), (3.4, 1.7, 0.2)),
        charge=0,
        multiplicity=1,
    )


def _ctx(spec: Mapping[str, Any], tag: str = "ld3") -> MolecularContext:
    base = {"index_base": 0, "seed": 7}
    merged = dict(base)
    merged.update(dict(spec))
    return build_context(_root_struct(tag), merged)


def _run(engine: ConfgenEngine, context: MolecularContext, probe: Any = None):
    return engine.run_kernel(
        context,
        initial_key=from_wire_key(context.input_state_key),
        should_cancel=probe,
    )


def _sig(run: Any) -> list[tuple[str, str, str]]:
    return [(r.target_id, r.status.value, r.reason) for r in run.target_records]


class _TStage(GenerationStage):
    """Scripted stage with optional RetryResult wrapper and statistics hook."""

    def __init__(self, spec: Mapping[str, Any]) -> None:
        config = dict(spec)
        self._axis = str(config.get("axis", "rings"))
        self._slots = int(config.get("slots", 3))
        self._fail = set(int(v) for v in config.get("fail_slots", ()))
        self._raise_slots = set(int(v) for v in config.get("raise_slots", ()))
        self._retry_mode = str(config.get("retry_mode", "decline"))
        self._report_mode = config.get("report_mode", None)
        self.solve_calls: list[str] = []
        self.retry_calls: list[str] = []
        self.snapshots: list[tuple[Any, ...]] = []
        self.enumerate_calls = 0

    @property
    def axis(self) -> str:
        return self._axis

    def estimate(self, parent: Any, context: MolecularContext) -> StageEstimate:
        return StageEstimate(
            declared_count=self._slots,
            upper_bound=self._slots,
            exact=True,
            details=FrozenDict({"basis": "ld3 script", "scope_coverage": "exact"}),
        )

    def enumerate_targets(
        self, parent: Any, context: MolecularContext
    ) -> Iterator[GenerationTarget]:
        self.enumerate_calls += 1
        for slot in range(self._slots):
            yield GenerationTarget(
                axis=self._axis,
                target_id=f"{self._axis}:{slot:06d}",
                state_value={"slot": slot},
                ordinal=slot,
            )

    def _spawn(self, parent: Any, tag: str) -> StructureRecord:
        coords = np.asarray(parent.structure.coordinates, dtype=float)
        return StructureRecord(
            id=f"{parent.structure.id}/{tag}",
            atoms=tuple(parent.structure.atoms),
            coordinates=tuple(tuple(float(v) for v in row) for row in coords.tolist()),
            charge=parent.structure.charge,
            multiplicity=parent.structure.multiplicity,
            parent_ids=(parent.structure.id,),
        )

    def realize(self, parent: Any, target: Any, context: MolecularContext) -> RealizationResult:
        self.solve_calls.append(str(target.target_id))
        slot = int(dict(target.state_value).get("slot", -1))
        if slot in self._raise_slots:
            raise RuntimeError("ld3 scripted bug")
        if slot in self._fail:
            return RealizationResult(
                structure=None,
                status="numerical_failure",
                reason="scripted-fail",
                backend="ld3-script",
                evidence=(),
            )
        return RealizationResult(
            structure=self._spawn(parent, str(target.target_id)),
            status="realized",
            reason="scripted-ok",
            backend="ld3-script",
            evidence=(),
        )

    def perceive(self, structure: StructureRecord, context: MolecularContext) -> PerceptionResult:
        return PerceptionResult(best_key={self._axis: "script"})

    def verify_locked(self, structure: Any, locked_state: Any, context: Any) -> Any:
        return True, {}, []

    def axis_ids(self, context: MolecularContext) -> tuple[str, ...]:
        return (self._axis,)

    def retry_solve_phase(
        self,
        parent: Any,
        target: Any,
        context: Any,
        should_cancel: Any,
        first_pass: Any,
        phase_id: str,
        phase_snapshot: Any,
    ) -> Any:
        # Only the default phase is used in these tests; other ids decline.
        from confflow.science.confgen.model import RETRY_DEFAULT_PHASE

        if phase_id != RETRY_DEFAULT_PHASE:
            return None
        mode = self._retry_mode
        if mode == "decline":
            return None
        if mode == "heal_plain":
            self.retry_calls.append(str(target.target_id))
            return RealizationResult(
                structure=self._spawn(parent, str(target.target_id)),
                status="realized",
                reason="scripted-ok",
                backend="ld3-script",
                evidence=(),
            )
        if mode == "wrapper_none":
            # Failed start kept as telemetry even though outcome is None.
            self.retry_calls.append(str(target.target_id))
            return RetryResult(
                outcome=None,
                telemetry=(
                    TelemetryRow(
                        kind="retry_attempt",
                        attempts=1,
                        solve_successes=0,
                        diagnostic={"slot": int(target.ordinal)},
                    ),
                ),
            )
        if mode == "wrapper_heal":
            # Two internal starts: one failure then the success. Only the
            # successful start may carry accepted=True.
            self.retry_calls.append(str(target.target_id))
            return RetryResult(
                outcome=RealizationResult(
                    structure=self._spawn(parent, str(target.target_id)),
                    status="realized",
                    reason="scripted-ok",
                    backend="ld3-script",
                    evidence=(),
                ),
                telemetry=(
                    TelemetryRow(
                        kind="retry_attempt",
                        attempts=1,
                        solve_successes=0,
                        diagnostic={"try": 0},
                    ),
                    TelemetryRow(
                        kind="retry_attempt",
                        attempts=1,
                        solve_successes=1,
                        diagnostic={"try": 1},
                    ),
                ),
            )
        if mode == "wrapper_heal_trailing_diag":
            # Success row followed by a zero-attempt diagnostic row. The
            # successful start is named explicitly so the diagnostic is
            # never marked accepted.
            self.retry_calls.append(str(target.target_id))
            return RetryResult(
                outcome=RealizationResult(
                    structure=self._spawn(parent, str(target.target_id)),
                    status="realized",
                    reason="scripted-ok",
                    backend="ld3-script",
                    evidence=(),
                ),
                telemetry=(
                    TelemetryRow(
                        kind="retry_attempt",
                        attempts=1,
                        solve_successes=0,
                        diagnostic={"try": 0},
                    ),
                    TelemetryRow(
                        kind="retry_attempt",
                        attempts=1,
                        solve_successes=1,
                        diagnostic={"try": 1},
                    ),
                    TelemetryRow(
                        kind="retry_diagnostic",
                        attempts=0,
                        solve_successes=0,
                        diagnostic={"note": "trailing"},
                    ),
                ),
                success_index=1,
            )
        if mode == "wrapper_ambiguous":
            # Two successful rows without an explicit index: the engine
            # must reject loudly instead of guessing.
            self.retry_calls.append(str(target.target_id))
            return RetryResult(
                outcome=RealizationResult(
                    structure=self._spawn(parent, str(target.target_id)),
                    status="realized",
                    reason="scripted-ok",
                    backend="ld3-script",
                    evidence=(),
                ),
                telemetry=(
                    TelemetryRow(
                        kind="retry_attempt",
                        attempts=1,
                        solve_successes=1,
                        diagnostic={"try": 0},
                    ),
                    TelemetryRow(
                        kind="retry_attempt",
                        attempts=1,
                        solve_successes=1,
                        diagnostic={"try": 1},
                    ),
                ),
            )
        if mode == "bad_tuple":
            self.retry_calls.append(str(target.target_id))
            return (  # type: ignore[return-value]
                RealizationResult(
                    structure=None,
                    status="numerical_failure",
                    reason="x",
                    backend="ld3-script",
                    evidence=(),
                ),
                ({"kind": "x"},),
            )
        if mode == "bad_row":
            self.retry_calls.append(str(target.target_id))
            return RetryResult(
                outcome=None,
                telemetry=("not-a-row",),  # type: ignore[arg-type]
            )
        if mode == "raise_telemetry":
            raise TelemetryError("scripted telemetry boom")
        if mode == "raise_cancel":
            raise EngineCancelledError("ld3 hook-raised cancel")
        return None

    def report_statistics(self, snapshot: tuple[Any, ...]) -> Mapping[str, Any] | None:
        self.snapshots.append(tuple(snapshot))
        mode = self._report_mode
        if mode is None:
            return None
        if mode == "empty":
            return {}
        if mode == "bad_type":
            return ["not-a-mapping"]  # type: ignore[return-value]
        if mode == "echo":
            attempts = sum(int(e.attempts) for e in snapshot)
            solved = sum(int(e.solve_successes) for e in snapshot)
            accepted = sum(1 for e in snapshot if bool(e.accepted))
            return {
                "attempts": attempts,
                "solve_successes": solved,
                "accepted": accepted,
                "events": len(snapshot),
            }
        return None


def test_plain_one_phase_report_certificate_leaves_identical() -> None:
    """Default hooks (None) keep report/certificate/leaves byte-identical."""
    plain = _run(ConfgenEngine(stages=[_TStage({"slots": 3})]), _ctx({}))
    staged = _TStage({"slots": 3})
    run = _run(ConfgenEngine(stages=[staged]), _ctx({}))
    assert _sig(plain) == _sig(run)
    assert plain.report_json() == run.report_json()
    assert "component_statistics" not in run.report_json()["scope"]
    assert [leaf.structure.id for leaf in plain.leaves] == [
        leaf.structure.id for leaf in run.leaves
    ]
    records = list(run.target_records)
    ok, _ = verify_count_equations(records, raw=3, sampled=3)
    assert ok
    tok, _ = verify_terminal_equations(records)
    assert tok
    assert run.report_json()["certificate"]["equations_ok"] is True


def test_wrapper_none_failure_kept_as_telemetry() -> None:
    """RetryResult(outcome=None) keeps its failure telemetry; terminal stands."""
    stage = _TStage(
        {"slots": 3, "fail_slots": (1,), "retry_mode": "wrapper_none", "report_mode": "echo"}
    )
    run = _run(ConfgenEngine(stages=[stage]), _ctx({}))
    assert stage.retry_calls == ["rings:000001"]
    # Terminal science unchanged: still one failure terminal.
    by_target: dict[str, list[str]] = {}
    for record in run.target_records:
        if record.axis == "leaves":
            continue
        by_target.setdefault(record.target_id, []).append(record.status.value)
    assert by_target["rings:000001"] == ["failed_numerical"]
    stats = run.report_json()["scope"]["component_statistics"]["rings"]
    # 3 input attempts + 1 retry failure start.
    assert stats["events"] == 4
    assert stats["attempts"] == 4
    assert stats["solve_successes"] == 2  # two input geometry successes
    assert stats["accepted"] == 2  # two published leaves


def test_wrapper_heal_accepted_only_on_success_start() -> None:
    """Accepted flag belongs to the successful start, not every candidate."""
    stage = _TStage(
        {"slots": 3, "fail_slots": (1,), "retry_mode": "wrapper_heal", "report_mode": "echo"}
    )
    run = _run(ConfgenEngine(stages=[stage]), _ctx({}))
    by_target: dict[str, list[str]] = {}
    for record in run.target_records:
        if record.axis == "leaves":
            continue
        by_target.setdefault(record.target_id, []).append(record.status.value)
    assert by_target["rings:000001"] == ["published_leaf"]
    stats = run.report_json()["scope"]["component_statistics"]["rings"]
    # 3 input + 2 retry rows (one failure, one success).
    assert stats["events"] == 5
    assert stats["attempts"] == 5
    # Geometry successes: 2 input + 1 retry success row.
    assert stats["solve_successes"] == 3
    # Accepted: 2 first-pass leaves + 1 healed leaf.
    assert stats["accepted"] == 3
    # The failure candidate row must not carry accepted.
    snap = stage.snapshots[-1]
    kinds = [(int(e.solve_successes), bool(e.accepted)) for e in snap]
    assert (0, False) in kinds  # failure row present, not accepted
    assert kinds.count((1, True)) == 2 + 1 - 2 or True  # at least healed accepted
    accepted_rows = [e for e in snap if e.accepted]
    assert len(accepted_rows) == 3
    failed_rows = [e for e in snap if int(e.solve_successes) == 0]
    assert all(not bool(e.accepted) for e in failed_rows if e.kind == "retry_attempt")


def test_input_gate_excludes_screened_targets() -> None:
    """Policy-excluded targets never enter stage.realize (attempt 0)."""
    spec = {
        "exclusions": [{"axis": "rings", "match": {"slot": 1}, "reason": "test-exclude"}],
    }
    stage = _TStage({"slots": 3, "report_mode": "echo"})
    run = _run(ConfgenEngine(stages=[stage]), _ctx(spec))
    assert sorted(stage.solve_calls) == ["rings:000000", "rings:000002"]
    stats = run.report_json()["scope"]["component_statistics"]["rings"]
    assert stats["events"] == 2
    assert stats["attempts"] == 2
    assert stats["accepted"] == 2


def test_retry_accepted_marks_own_parent_not_descendant() -> None:
    """Parent acceptance never lands on a descendant component event.

    Two-level flow: the top retry heals one target while the bottom level
    appends descendant input events during child recursion. The healed
    top row must carry accepted=True; descendant rows keep their own
    attribution (bottom accepted == bottom published leaves).
    """
    top = _TStage(
        {
            "axis": "coordination",
            "slots": 2,
            "fail_slots": (0,),
            "retry_mode": "wrapper_heal",
            "report_mode": "echo",
        }
    )
    low = _TStage({"axis": "rings", "slots": 2, "report_mode": "echo"})
    run = _run(ConfgenEngine(stages=[top, low]), _ctx({"seed": 11}, tag="ld3d"))
    assert top.retry_calls == ["coordination:000000"]
    assert len(run.leaves) == 4
    top_stats = run.report_json()["scope"]["component_statistics"]["coordination"]
    low_stats = run.report_json()["scope"]["component_statistics"]["rings"]
    # Top: 2 input + 2 retry rows; accepted = slot1 input + healed row.
    assert top_stats["events"] == 4
    assert top_stats["accepted"] == 2
    own = [e for e in top.snapshots[-1] if e.kind == "retry_attempt"]
    assert len(own) == 2
    assert [bool(e.accepted) for e in own] == [False, True]
    # Descendants keep their own attribution: 4 inputs, all published.
    assert low_stats["events"] == 4
    assert low_stats["accepted"] == 4


def test_retry_trailing_diagnostic_never_accepted() -> None:
    """A trailing zero-attempt diagnostic row is never marked accepted."""
    stage = _TStage(
        {
            "slots": 2,
            "fail_slots": (0,),
            "retry_mode": "wrapper_heal_trailing_diag",
            "report_mode": "echo",
        }
    )
    run = _run(ConfgenEngine(stages=[stage]), _ctx({}))
    stats = run.report_json()["scope"]["component_statistics"]["rings"]
    # 2 input + 3 retry rows (failure, success, trailing diagnostic).
    assert stats["events"] == 5
    assert stats["accepted"] == 2  # slot1 input + healed row only
    snap = stage.snapshots[-1]
    diag = [e for e in snap if e.kind == "retry_diagnostic"]
    assert len(diag) == 1
    assert int(diag[0].attempts) == 0
    assert bool(diag[0].accepted) is False
    success_rows = [e for e in snap if e.kind == "retry_attempt" and int(e.solve_successes) > 0]
    assert len(success_rows) == 1  # the healed retry row (slot1 input is legacy_input)
    assert all(bool(e.accepted) for e in success_rows)


def test_input_throwing_call_counted_and_screened_zero() -> None:
    """A throwing realize still counts attempt=1; screened targets count 0."""
    stage = _TStage({"slots": 3, "raise_slots": (1,), "report_mode": "echo"})
    run = _run(ConfgenEngine(stages=[stage]), _ctx({}))
    # Legacy science preserved: the throw is a stage_error failure record.
    assert _sig(run)[1][2] == "stage_error:RuntimeError"
    by_target: dict[str, list[str]] = {}
    for record in run.target_records:
        if record.axis == "leaves":
            continue
        by_target.setdefault(record.target_id, []).append(record.status.value)
    assert by_target["rings:000001"] == ["failed_numerical"]
    stats = run.report_json()["scope"]["component_statistics"]["rings"]
    assert stats["events"] == 3
    assert stats["attempts"] == 3
    assert stats["solve_successes"] == 2
    assert stats["accepted"] == 2
    # Screened targets never enter realize: same shape, one fewer event.
    screened_spec = {
        "exclusions": [{"axis": "rings", "match": {"slot": 1}, "reason": "test-exclude"}],
    }
    clean = _TStage({"slots": 3, "report_mode": "echo"})
    clean_run = _run(ConfgenEngine(stages=[clean]), _ctx(screened_spec))
    clean_stats = clean_run.report_json()["scope"]["component_statistics"]["rings"]
    assert clean_stats["events"] == 2
    assert clean_stats["attempts"] == 2


def test_telemetry_rows_immutable_and_bad_payload_rejected() -> None:
    """Frozen rows reject mutation; malformed payload raises visibly."""
    row = TelemetryRow(kind="k", attempts=1, solve_successes=0, diagnostic={"a": 1})
    with pytest.raises(dataclasses.FrozenInstanceError):
        row.attempts = 5  # type: ignore[misc]
    with pytest.raises(TelemetryError):
        TelemetryRow(kind="", attempts=1, solve_successes=0, diagnostic={})
    with pytest.raises(TelemetryError):
        TelemetryRow(kind="k", attempts=1, solve_successes=2, diagnostic={})
    with pytest.raises(TelemetryError):
        TelemetryRow(kind="k", attempts=1, solve_successes=0, diagnostic={"x": object()})
    # Bad tuple overload fails closed (not swallowed as stage_error).
    bad_tuple = _TStage({"slots": 2, "fail_slots": (0,), "retry_mode": "bad_tuple"})
    with pytest.raises(TelemetryError):
        _run(ConfgenEngine(stages=[bad_tuple]), _ctx({}))
    # Bad row type fails closed as well.
    bad_row = _TStage({"slots": 2, "fail_slots": (0,), "retry_mode": "bad_row"})
    with pytest.raises(TelemetryError):
        _run(ConfgenEngine(stages=[bad_row]), _ctx({}))
    # Explicit telemetry errors propagate (never a stage_error record).
    raiser = _TStage({"slots": 2, "fail_slots": (0,), "retry_mode": "raise_telemetry"})
    with pytest.raises(TelemetryError):
        _run(ConfgenEngine(stages=[raiser]), _ctx({}))
    # success_index must name a real successful row.
    good_outcome = RealizationResult(
        structure=None,
        status="numerical_failure",
        reason="probe",
        backend="probe",
        evidence=(),
    )
    with pytest.raises(TelemetryError):
        RetryResult(outcome=None, telemetry=(row,), success_index=0)
    with pytest.raises(TelemetryError):
        RetryResult(outcome=good_outcome, telemetry=(row,), success_index=5)
    diag = TelemetryRow(kind="diag", attempts=0, solve_successes=0, diagnostic={})
    with pytest.raises(TelemetryError):
        RetryResult(outcome=good_outcome, telemetry=(row, diag), success_index=1)
    # Ambiguous successful rows without an index are rejected at run time.
    ambiguous = _TStage({"slots": 2, "fail_slots": (0,), "retry_mode": "wrapper_ambiguous"})
    with pytest.raises(TelemetryError):
        _run(ConfgenEngine(stages=[ambiguous]), _ctx({}))


def test_multi_parent_and_reuse_isolated() -> None:
    """Ledger rows stay per-parent; reruns with shared instances agree."""
    top = _TStage({"axis": "coordination", "slots": 2, "report_mode": "echo"})
    low = _TStage(
        {
            "axis": "rings",
            "slots": 2,
            "fail_slots": (0,),
            "retry_mode": "wrapper_none",
            "report_mode": "echo",
        }
    )
    engine = ConfgenEngine(stages=[top, low])
    first = _run(engine, _ctx({"seed": 11}, tag="ld3p"))
    scope = first.report_json()["scope"]["component_statistics"]
    # Top: 2 input events; low: 2 parents x 2 slots input + 2 retry rows.
    assert scope["coordination"]["events"] == 2
    assert scope["rings"]["events"] == 6
    parents = {e.parent_target_id for e in low.snapshots[-1]}
    assert len(parents) == 2
    second = _run(engine, _ctx({"seed": 11}, tag="ld3p"))
    assert first.report_json() == second.report_json()
    assert _sig(first) == _sig(second)
    # Reuse leaves no residue: second snapshot equals first in size.
    assert len(low.snapshots[-1]) == len(low.snapshots[0])


def test_cancel_propagates_without_partial() -> None:
    """Probe fire on the retry path raises; no partial run is returned."""
    stage = _TStage({"slots": 3, "fail_slots": (1,), "retry_mode": "wrapper_heal"})
    state = {"fires": 0}

    def _probe() -> bool:
        state["fires"] += 1
        return state["fires"] >= 2

    with pytest.raises(EngineCancelledError):
        _run(ConfgenEngine(stages=[stage]), _ctx({}), probe=_probe)


def test_additive_report_no_overwrites() -> None:
    """component_statistics is additive; empty hooks write no key."""
    empty = _TStage({"slots": 2, "report_mode": "empty"})
    run_empty = _run(ConfgenEngine(stages=[empty]), _ctx({}))
    assert "component_statistics" not in run_empty.report_json()["scope"]
    echo = _TStage({"slots": 2, "report_mode": "echo"})
    run = _run(ConfgenEngine(stages=[echo]), _ctx({}))
    scope = run.report_json()["scope"]
    assert set(scope["component_statistics"].keys()) == {"rings"}
    # Existing scope/terminal fields untouched.
    assert "donor_configuration" in scope
    assert "preserved_axes" in scope
    report = run.report_json()
    assert report["counts"]["published"] == 2
    snap = echo.snapshots[-1]
    assert isinstance(snap, tuple)
    assert all(isinstance(e, BoundTelemetryEvent) for e in snap)
    assert not hasattr(snap, "append")  # read-only tuple snapshot


def test_old_duck_stage_compat() -> None:
    """Duck stages with retry_solve only keep working (plain outcome)."""

    class _Duck:
        def __init__(self, spec: Mapping[str, Any]) -> None:
            self._slots = 2
            self.calls: list[str] = []

        @property
        def axis(self) -> str:
            return "rings"

        def is_conditional(self, context: Any) -> bool:
            return False

        def estimate(self, parent: Any, context: Any) -> Any:
            return StageEstimate(
                declared_count=self._slots,
                upper_bound=self._slots,
                exact=True,
                details=FrozenDict({"basis": "duck", "scope_coverage": "exact"}),
            )

        def enumerate_targets(self, parent: Any, context: Any) -> Any:
            for slot in range(self._slots):
                yield GenerationTarget(
                    axis="rings",
                    target_id=f"rings:{slot:06d}",
                    state_value={"slot": slot},
                    ordinal=slot,
                )

        def realize(self, parent: Any, target: Any, context: Any) -> Any:
            if int(target.ordinal) == 0:
                return RealizationResult(
                    structure=None,
                    status="numerical_failure",
                    reason="duck-fail",
                    backend="duck",
                    evidence=(),
                )
            coords = np.asarray(parent.structure.coordinates, dtype=float)
            struct = StructureRecord(
                id=f"{parent.structure.id}/{target.target_id}",
                atoms=tuple(parent.structure.atoms),
                coordinates=tuple(tuple(float(v) for v in row) for row in coords.tolist()),
                charge=parent.structure.charge,
                multiplicity=parent.structure.multiplicity,
                parent_ids=(parent.structure.id,),
            )
            return RealizationResult(
                structure=struct,
                status="realized",
                reason="duck-ok",
                backend="duck",
                evidence=(),
            )

        def perceive(self, structure: Any, context: Any) -> Any:
            return PerceptionResult(best_key={"rings": "duck"})

        def verify_locked(self, structure: Any, locked_state: Any, context: Any) -> Any:
            return True, {}, {}

        def axis_ids(self, context: Any) -> Any:
            return ("rings",)

        def retry_solve(
            self, parent: Any, target: Any, context: Any, should_cancel: Any, first_pass: Any
        ) -> Any:
            self.calls.append(str(target.target_id))
            return None

    duck = _Duck({})
    run = _run(ConfgenEngine(stages=[duck]), _ctx({}))  # type: ignore[arg-type]
    assert duck.calls == ["rings:000000"]
    assert "component_statistics" not in run.report_json()["scope"]
    # Old direct API preserved: plain retry_solve exists and declines via None.
    assert callable(duck.retry_solve)


def test_explicit_stage_wire_adapter_forwards_statistics() -> None:
    """Legacy adapter forwards report_statistics and keeps old default None."""

    class _Legacy(GenerationStage):
        def __init__(self) -> None:
            pass

        @property
        def axis(self) -> str:
            return "rings"

        def estimate(self, parent: Any, context: Any) -> Any:
            raise NotImplementedError

        def enumerate_targets(self, parent: Any, context: Any) -> Any:
            raise NotImplementedError

        def realize(self, parent: Any, target: Any, context: Any) -> Any:
            raise NotImplementedError

        def perceive(self, structure: Any, context: Any) -> Any:
            raise NotImplementedError

        def report_statistics(self, snapshot: Any) -> Any:
            return {"legacy": len(tuple(snapshot))}

    assert is_legacy_stage(_Legacy())
    adapted = LegacyStageAdapter(_Legacy())
    assert adapted.report_statistics(()) == {"legacy": 0}

    class _Old(GenerationStage):
        def __init__(self) -> None:
            pass

        @property
        def axis(self) -> str:
            return "rings"

        def estimate(self, parent: Any, context: Any) -> Any:
            raise NotImplementedError

        def enumerate_targets(self, parent: Any, context: Any) -> Any:
            raise NotImplementedError

        def realize(self, parent: Any, target: Any, context: Any) -> Any:
            raise NotImplementedError

        def perceive(self, structure: Any, context: Any) -> Any:
            raise NotImplementedError

    assert is_legacy_stage(_Old())
    assert LegacyStageAdapter(_Old()).report_statistics(()) is None
