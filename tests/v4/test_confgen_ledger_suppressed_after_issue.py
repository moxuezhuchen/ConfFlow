#!/usr/bin/env python3
"""F-ledger: suppressed-after-issue counts as issued, never as skipped."""

from __future__ import annotations

import numpy as np
import pytest

from confflow.domain import FrozenDict
from confflow.science.confgen import build_context
from confflow.science.confgen.accounting import TargetRecord, TerminalStatus, attempt_ledger_counts
from confflow.science.confgen.engine import ConfgenEngine, EngineCancelledError
from confflow.science.confgen.kernel_records import KernelGenerationTarget
from confflow.science.confgen.model import PerceptionResult, RealizationResult, StageEstimate
from confflow.science.confgen.wire_v3 import from_wire_key
from tests.v4._helpers.repair import _butane


class _SpyLedgerStage:
    axis = "coordination"

    def __init__(self) -> None:
        self.realize_calls: list[str] = []
        self.retry_calls: list[str] = []

    def is_conditional(self, context) -> bool:  # type: ignore[no-untyped-def]
        return False

    def axis_ids(self, context):  # type: ignore[no-untyped-def]
        return ("coordination",)

    def estimate(self, parent, context):  # type: ignore[no-untyped-def]
        return StageEstimate(
            declared_count=3,
            upper_bound=3,
            exact=True,
            details=FrozenDict({"basis": "spy-ledger", "scope_coverage": "exact"}),
        )

    def enumerate_targets(self, parent, context):  # type: ignore[no-untyped-def]
        for i in range(3):
            yield KernelGenerationTarget(
                axis="coordination",
                target_id=f"coordination:{i:06d}",
                state_value={"mode": f"M{i}"},
                ordinal=i,
            )

    def _mk(self, parent, target):  # type: ignore[no-untyped-def]
        from confflow.domain.structure import StructureRecord

        coords = np.asarray(parent.structure.coordinates, dtype=float) + np.array(
            [float(target.ordinal) * 10.0, 0, 0]
        )
        return StructureRecord(
            id=str(target.target_id),
            atoms=tuple(parent.structure.atoms),
            coordinates=tuple(tuple(p) for p in coords.tolist()),
            charge=0,
            multiplicity=1,
            parent_ids=(parent.structure.id,),
            role="spy-ledger",
            ordinal=int(target.ordinal),
        )

    def realize(self, parent, target, context):  # type: ignore[no-untyped-def]
        self.realize_calls.append(str(target.target_id))
        if str(target.target_id) in ("coordination:000000", "coordination:000001"):
            return RealizationResult(
                structure=None,
                status="numerical_failure",
                reason="spy-input-fail",
                backend="spy-ledger",
                evidence=({},),
            )
        return RealizationResult(
            structure=self._mk(parent, target),
            status="realized",
            reason="spy-ok",
            backend="spy-ledger",
            evidence=({},),
        )

    def perceive(self, structure, context):  # type: ignore[no-untyped-def]
        try:
            ordv = int(str(structure.id).split(":")[-1])
        except Exception:
            ordv = 0
        return PerceptionResult(best_key={"coordination": f"M{ordv}"}, confidence="reported")

    def suppression_for_target(self, parent, target, context):  # type: ignore[no-untyped-def]
        if str(target.target_id) == "coordination:000001":
            return {
                "suppressed_target": str(target.target_id),
                "representative_target": "coordination:000000",
                "orbit_id": "o1",
                "rho": [1, 0],
                "site_action": {"s0": "s1"},
                "rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                "pair_residual": 0.0,
                "witness_provenance": "spy-ledger",
                "stereo_centers_audited": [],
                "closure_order": 2,
            }
        return None

    def retry_solve(self, parent, target, context, should_cancel, first_pass):  # type: ignore[no-untyped-def]
        self.retry_calls.append(str(target.target_id))
        if str(target.target_id) == "coordination:000000":
            return RealizationResult(
                structure=self._mk(parent, target),
                status="realized",
                reason="spy-retry-ok",
                backend="spy-ledger",
                evidence=({},),
            )
        return None


def _run_spy():  # type: ignore[no-untyped-def]
    ctx = build_context(_butane("seed"), {"index_base": 0, "seed": 21})
    stage = _SpyLedgerStage()
    eng = ConfgenEngine()
    run = eng.run_kernel(
        ctx, initial_key=from_wire_key(ctx.input_state_key), stage_overrides=[stage]
    )
    return stage, run


def test_suppressed_after_issue_counts_as_issued() -> None:
    stage, run = _run_spy()
    assert stage.realize_calls == [
        "coordination:000000",
        "coordination:000001",
        "coordination:000002",
    ]
    assert [r.status for r in run.target_records] == [
        TerminalStatus.PUBLISHED_LEAF,
        TerminalStatus.SUPPRESSED_BY_VERIFIED_SYMMETRY,
        TerminalStatus.PUBLISHED_LEAF,
    ]
    rep = run.report_json()
    ledger = rep["attempt_ledger"]
    assert ledger["issued_attempts"] == 3
    assert ledger["suppressed_skipped"] == 0
    assert ledger["suppressed_after_issue"] == 1
    assert ledger["by_status"]["suppressed_by_verified_symmetry"] == 1
    assert ledger["attempted_without_success"] == 1
    assert (
        rep["leaf_certificate"]["leaf_categories"]["REALIZATION_SUPPRESSED_BY_VERIFIED_SYMMETRY"]
        == 1
    )
    assert rep["realization"]["realization_attempts"] == 3
    assert rep["certificate"]["equations_ok"] is True


def test_legacy_counts_without_history_preserves_skip() -> None:
    recs = [
        TargetRecord(
            target_id="a:000000",
            axis="a",
            ordinal=0,
            status=TerminalStatus.PUBLISHED_LEAF,
            reason="published_leaf",
            complete_key=FrozenDict({"x": 1}),
        ),
        TargetRecord(
            target_id="a:000001",
            axis="a",
            ordinal=1,
            status=TerminalStatus.SUPPRESSED_BY_VERIFIED_SYMMETRY,
            reason="suppressed_by_verified_symmetry:representative=a:000000",
            complete_key=FrozenDict({"x": 2}),
        ),
    ]
    old = attempt_ledger_counts(recs)
    assert old["issued_attempts"] == 1
    assert old["suppressed_skipped"] == 1
    assert "suppressed_after_issue" not in old
    new = attempt_ledger_counts(recs, issued_history={(None, "a", 1)})
    assert new["issued_attempts"] == 2
    assert new["suppressed_skipped"] == 0
    assert new["suppressed_after_issue"] == 1


def test_no_retry_report_has_no_new_key() -> None:
    ctx = build_context(_butane("seed"), {"index_base": 0, "seed": 21})
    from tests.v4.test_confgen_v3_core import _ShiftStage

    stages = [_ShiftStage({"axis": "coordination", "states": [("A", 0.0, 0.0, 0.0)]})]
    run = ConfgenEngine(stages=stages).run(ctx)
    ledger = run.report.thaw()["attempt_ledger"]
    assert "suppressed_after_issue" not in ledger
    assert ledger["suppressed_skipped"] == 0


def test_stage_error_still_issued_and_not_retryable() -> None:
    from tests.v4.test_confgen_v3_core import _ShiftStage

    ctx = build_context(_butane("seed"), {"index_base": 0, "seed": 21})

    class _RetryProbe(_ShiftStage):
        def retry_solve(self, parent, target, context, should_cancel, first_pass):  # type: ignore[no-untyped-def]
            raise AssertionError("stage_error must never retry")

    probe = _RetryProbe(
        {
            "axis": "coordination",
            "states": [("A", 0.0, 0.0, 0.0), ("B", 20.0, 0.0, 0.0)],
            "fail_on": ["A"],
        }
    )
    run = ConfgenEngine(stages=[probe]).run(ctx)
    ledger = run.report.thaw()["attempt_ledger"]
    assert ledger["issued_attempts"] == 2
    assert ledger["attempted_without_success"] == 1


def test_retry_cancellation_propagates_without_partial() -> None:
    stage = _SpyLedgerStage()
    ctx = build_context(_butane("seed"), {"index_base": 0, "seed": 21})
    eng = ConfgenEngine()

    def _probe() -> bool:
        return True

    with pytest.raises(EngineCancelledError):
        eng.run_kernel(
            ctx,
            initial_key=from_wire_key(ctx.input_state_key),
            should_cancel=_probe,
            stage_overrides=[stage],
        )
