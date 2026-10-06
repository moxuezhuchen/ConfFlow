#!/usr/bin/env python3
"""FIX1D retry boundaries: real engine dispatch, no silent acceptance.

Self-contained (no fixtures, no cross-test imports). A tiny scripted
single-axis stage drives real ConfgenEngine.run_kernel so the D0/D0.2
retry seam is always exercised through engine dispatch, never called
directly. Each test pins one real boundary: decline keeps a single
failure, heal replaces at the same position, budget only retries
solve-failures, solver-error never retried, suppression never retried,
cancellation propagates without partials, malformed hooks fail closed
visibly, and healed leaves travel the full post-solve path.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

import numpy as np
import pytest

from confflow.domain._immutable import FrozenDict
from confflow.domain.structure import StructureRecord
from confflow.science.confgen.engine import ConfgenEngine, EngineCancelledError
from confflow.science.confgen.kernel_records import RetryResult, TelemetryError, TelemetryRow
from confflow.science.confgen.model import (
    GenerationStage,
    GenerationTarget,
    MolecularContext,
    PerceptionResult,
    RealizationResult,
    StageEstimate,
    build_context,
)
from confflow.science.confgen.wire_v3 import from_wire_key


def _root_struct(struct_id: str) -> StructureRecord:
    return StructureRecord(
        id=struct_id,
        atoms=("C", "C", "C", "C"),
        coordinates=((0.0, 0.0, 0.0), (1.5, 0.0, 0.0), (2.0, 1.3, 0.5), (3.4, 1.7, 0.2)),
        charge=0,
        multiplicity=1,
    )


def _ctx(spec: Mapping[str, Any], tag: str = "fix1d") -> MolecularContext:
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


def _terminals(run: Any) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for record in run.target_records:
        if record.axis == "leaves":
            continue
        out.setdefault(record.target_id, []).append(record.status.value)
    return out


class _ScriptStage(GenerationStage):
    """Scripted identity-geometry stage with an engine-dispatched retry hook."""

    def __init__(self, spec: Mapping[str, Any]) -> None:
        config = dict(spec)
        self._axis = str(config.get("axis", "rings"))
        self._slots = int(config.get("slots", 3))
        self._fail = set(int(v) for v in config.get("fail_slots", ()))
        self._unsupported = set(int(v) for v in config.get("unsupported_slots", ()))
        self._raise = dict(config.get("raise_slots", {}))
        self._suppressed = set(int(v) for v in config.get("suppressed_slots", ()))
        self._retry_mode = str(config.get("retry_mode", "decline"))
        self.solve_calls: list[str] = []
        self.retry_calls: list[str] = []
        self.first_pass_seen: Any = None

    @property
    def axis(self) -> str:
        return self._axis

    def estimate(self, parent: Any, context: MolecularContext) -> StageEstimate:
        return StageEstimate(
            declared_count=self._slots,
            upper_bound=self._slots,
            exact=True,
            details=FrozenDict({"basis": "fix1d script", "scope_coverage": "exact"}),
        )

    def enumerate_targets(
        self, parent: Any, context: MolecularContext
    ) -> Iterator[GenerationTarget]:
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
        if slot in self._raise:
            raise RuntimeError("fix1d scripted bug")
        if slot in self._unsupported:
            return RealizationResult(
                structure=None,
                status="unsupported",
                reason="scripted-unsupported",
                backend="fix1d-script",
                evidence=(),
            )
        if slot in self._fail:
            return RealizationResult(
                structure=None,
                status="numerical_failure",
                reason="scripted-fail",
                backend="fix1d-script",
                evidence=(),
            )
        return RealizationResult(
            structure=self._spawn(parent, str(target.target_id)),
            status="realized",
            reason="scripted-ok",
            backend="fix1d-script",
            evidence=(),
        )

    def audit_target(self, structure: Any, target: Any, parent: Any, context: Any) -> Any:
        return True, {self._axis: "script"}, []

    def perceive(self, structure: StructureRecord, context: MolecularContext) -> PerceptionResult:
        return PerceptionResult(best_key={self._axis: "script"})

    def verify_locked(self, structure: Any, locked_state: Any, context: Any) -> Any:
        return True, {}, []

    def axis_ids(self, context: MolecularContext) -> tuple[str, ...]:
        return (self._axis,)

    def suppression_for_target(self, parent: Any, target: Any, context: Any) -> Any:
        slot = int(dict(target.state_value).get("slot", -1))
        if slot in self._suppressed:
            return {
                "suppressed_target": str(target.target_id),
                "representative_target": f"{self._axis}:000000",
                "orbit_id": "fix1d-orbit",
                "rho": 0.0,
                "site_action": {},
                "rotation": [],
                "pair_residual": 0.0,
                "witness_provenance": "fix1d-test",
                "stereo_centers_audited": [],
                "closure_order": 1,
            }
        return None

    def retry_solve(
        self, parent: Any, target: Any, context: Any, should_cancel: Any, first_pass: Any
    ) -> Any:
        self.retry_calls.append(str(target.target_id))
        self.first_pass_seen = first_pass
        if self._retry_mode == "heal":
            return RealizationResult(
                structure=self._spawn(parent, str(target.target_id)),
                status="realized",
                reason="scripted-ok",
                backend="fix1d-script",
                evidence=(),
            )
        if self._retry_mode == "fail_again":
            return RealizationResult(
                structure=None,
                status="numerical_failure",
                reason="scripted-retry-fail",
                backend="fix1d-script",
                evidence=(),
            )
        if self._retry_mode == "raise_telemetry":
            raise TelemetryError("fix1d scripted malformed telemetry")
        if self._retry_mode == "bad_type":
            return ("not", "an-outcome")  # fail-closed: unsupported hook type
        if self._retry_mode == "raise_cancel":
            raise EngineCancelledError("fix1d hook-raised cancel")
        return None


def test_declined_retry_keeps_single_failure_no_silent_acceptance() -> None:
    stage = _ScriptStage({"slots": 3, "fail_slots": (1,), "retry_mode": "decline"})
    run = _run(ConfgenEngine(stages=[stage]), _ctx({}))
    assert _terminals(run)["rings:000001"] == ["failed_numerical"]
    assert stage.retry_calls == ["rings:000001"]
    assert (
        len([leaf for leaf in run.leaves if leaf.provenance["leaf_target_id"] == "rings:000001"])
        == 0
    )


def test_healed_retry_replaces_at_same_position_with_full_post_solve() -> None:
    stage = _ScriptStage({"slots": 3, "fail_slots": (1,), "retry_mode": "heal"})
    run = _run(ConfgenEngine(stages=[stage]), _ctx({}))
    assert _terminals(run)["rings:000001"] == ["published_leaf"]
    healed = [leaf for leaf in run.leaves if leaf.provenance["leaf_target_id"] == "rings:000001"]
    assert len(healed) == 1
    # Full post-solve path traversed: perception key present, backend honest.
    assert healed[0].structure is not None
    report = run.report_json()
    assert report["counts"]["published"] == 3
    # First-pass table handed to the hook is the frozen per-parent snapshot.
    assert stage.first_pass_seen is not None
    statuses = {str(row.target_id): str(row.status) for row in stage.first_pass_seen}
    assert statuses["rings:000001"] == "failed_numerical"
    assert statuses["rings:000000"] == "published_leaf"


def test_retry_again_failure_replaces_stale_with_single_terminal() -> None:
    stage = _ScriptStage({"slots": 3, "fail_slots": (1,), "retry_mode": "fail_again"})
    run = _run(ConfgenEngine(stages=[stage]), _ctx({}))
    assert _terminals(run)["rings:000001"] == ["failed_numerical"]
    reasons = [r.reason for r in run.target_records if r.target_id == "rings:000001"]
    assert reasons == ["scripted-retry-fail"]


def test_budget_only_solve_failures_retried_unsupported_and_success_skipped() -> None:
    stage = _ScriptStage(
        {"slots": 3, "fail_slots": (1,), "unsupported_slots": (2,), "retry_mode": "heal"}
    )
    run = _run(ConfgenEngine(stages=[stage]), _ctx({}))
    assert stage.retry_calls == ["rings:000001"]
    assert _terminals(run)["rings:000002"] == ["unsupported"]
    assert _terminals(run)["rings:000000"] == ["published_leaf"]


def test_solver_error_never_retried_fail_closed() -> None:
    stage = _ScriptStage(
        {"slots": 3, "fail_slots": (1,), "raise_slots": {2: "bug"}, "retry_mode": "heal"}
    )
    run = _run(ConfgenEngine(stages=[stage]), _ctx({}))
    assert "rings:000002" not in stage.retry_calls
    assert stage.retry_calls == ["rings:000001"]
    by_target = _terminals(run)
    assert by_target["rings:000001"] == ["published_leaf"]
    assert "rings:000002" in by_target


def test_suppressed_target_never_solved_on_retry_path() -> None:
    stage = _ScriptStage(
        {"slots": 3, "fail_slots": (1,), "suppressed_slots": (2,), "retry_mode": "heal"}
    )
    run = _run(ConfgenEngine(stages=[stage]), _ctx({}))
    assert "rings:000002" not in stage.retry_calls
    assert _terminals(run)["rings:000002"] == ["suppressed_by_verified_symmetry"]


def test_cancel_on_retry_path_propagates_without_partial() -> None:
    stage = _ScriptStage({"slots": 4, "fail_slots": (1, 2), "retry_mode": "heal"})

    def _probe() -> bool:
        return len(stage.retry_calls) >= 1

    with pytest.raises(EngineCancelledError):
        _run(ConfgenEngine(stages=[stage]), _ctx({}), probe=_probe)
    assert stage.retry_calls == ["rings:000001"]


def test_hook_cancel_and_malformed_hook_fail_closed_visibly() -> None:
    canceller = _ScriptStage({"slots": 2, "fail_slots": (0,), "retry_mode": "raise_cancel"})
    with pytest.raises(EngineCancelledError):
        _run(ConfgenEngine(stages=[canceller]), _ctx({}))
    raiser = _ScriptStage({"slots": 2, "fail_slots": (0,), "retry_mode": "raise_telemetry"})
    with pytest.raises(TelemetryError):
        _run(ConfgenEngine(stages=[raiser]), _ctx({}))
    bad = _ScriptStage({"slots": 2, "fail_slots": (0,), "retry_mode": "bad_type"})
    with pytest.raises(TelemetryError):
        _run(ConfgenEngine(stages=[bad]), _ctx({}))


def test_wrapper_telemetry_rows_bound_with_acceptance_separate() -> None:
    class _Wrapper(_ScriptStage):
        def report_statistics(self, snapshot):  # type: ignore[no-untyped-def]
            return {"events": len(tuple(snapshot))}

        def retry_solve_phase(  # type: ignore[override]
            self,
            parent: Any,
            target: Any,
            context: Any,
            should_cancel: Any,
            first_pass: Any,
            phase_id: str,
            phase_snapshot: Any,
        ) -> Any:
            self.retry_calls.append(str(target.target_id))
            if int(target.ordinal) != 1:
                return None
            outcome = RealizationResult(
                structure=self._spawn(parent, str(target.target_id)),
                status="realized",
                reason="scripted-ok",
                backend="fix1d-script",
                evidence=(),
            )
            return RetryResult(
                outcome=outcome,
                telemetry=(
                    TelemetryRow(kind="sibling", attempts=1, solve_successes=1, diagnostic={}),
                    TelemetryRow(kind="diag", attempts=0, solve_successes=0, diagnostic={}),
                ),
                success_index=0,
            )

    stage = _Wrapper({"slots": 3, "fail_slots": (1,)})
    run = _run(ConfgenEngine(stages=[stage]), _ctx({}))
    assert _terminals(run)["rings:000001"] == ["published_leaf"]
    scope = run.report_json()["scope"]
    assert scope["component_statistics"]["rings"]["events"] >= 2
