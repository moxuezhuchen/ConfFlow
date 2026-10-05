#!/usr/bin/env python3
"""D0 two-pass seam rehearsal: generic optional batch hook, real engine runs.

Self-contained (no fixtures, no cross-test imports, no TS1 solve). Every
test drives a real ``ConfgenEngine.run_kernel``; the batch hook below is
always exercised through engine dispatch, never called directly.

Semantics locked here (root review): one terminal record per target;
a retried outcome REPLACES its stale failure (deferred subtree revoked,
counters reconciled) at the exact position; first-pass publications stand
byte-identical; cancel propagates only on the retry path.
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
from confflow.science.confgen.model import (
    GenerationStage,
    GenerationTarget,
    MolecularContext,
    PerceptionResult,
    RealizationResult,
    RetryFirstPass,
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


def _ctx(spec: Mapping[str, Any], tag: str = "d0") -> MolecularContext:
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


def _terminals_by_target(run: Any) -> dict[str, list[str]]:
    by_target: dict[str, list[str]] = {}
    for record in run.target_records:
        if record.axis == "leaves":
            continue
        by_target.setdefault(record.target_id, []).append(record.status.value)
    return by_target


class _ScriptStage(GenerationStage):
    """Scripted single-axis stage; identity geometry, scripted failures."""

    def __init__(self, spec: Mapping[str, Any]) -> None:
        config = dict(spec)
        self._axis = str(config.get("axis", "rings"))
        self._slots = int(config.get("slots", 3))
        self._fail = set(int(v) for v in config.get("fail_slots", ()))
        self._retry_mode = str(config.get("retry_mode", "decline"))
        self._retry_by_slot = {
            int(k): str(v) for k, v in dict(config.get("retry_by_slot", {})).items()
        }
        self._representative = config.get("representative")
        self._raise = dict(config.get("raise_slots", {}))
        self._drift = set(int(v) for v in config.get("drift_slots", ()))
        self.solve_calls: list[str] = []
        self.retry_calls: list[str] = []
        self.retry_parents: list[str] = []
        self.retry_probe: Any = None
        self.first_pass_seen: Any = None
        self.enumerate_calls = 0

    @property
    def axis(self) -> str:
        return self._axis

    def estimate(self, parent: Any, context: MolecularContext) -> StageEstimate:
        return StageEstimate(
            declared_count=self._slots,
            upper_bound=self._slots,
            exact=True,
            details=FrozenDict({"basis": "d0 script", "scope_coverage": "exact"}),
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
        if slot in self._raise:
            if self._raise[slot] == "cancel":
                raise EngineCancelledError("d0 scripted cancel")
            raise RuntimeError("d0 scripted bug")
        if slot in self._fail:
            return RealizationResult(
                structure=None,
                status="numerical_failure",
                reason="scripted-fail",
                backend="d0-script",
                evidence=(),
            )
        return RealizationResult(
            structure=self._spawn(parent, str(target.target_id)),
            status="realized",
            reason="scripted-ok",
            backend="d0-script",
            evidence=(),
        )

    def audit_target(self, structure: Any, target: Any, parent: Any, context: Any) -> Any:
        if int(dict(target.state_value).get("slot", -1)) in self._drift:
            return False, {self._axis: "drifted"}, [{"kind": "drift", "detail": "d0"}]
        return True, {self._axis: "script"}, []

    def perceive(self, structure: StructureRecord, context: MolecularContext) -> PerceptionResult:
        return PerceptionResult(best_key={self._axis: "script"})

    def verify_locked(self, structure: Any, locked_state: Any, context: Any) -> Any:
        """Scaffolding: accept the carried discrete label (D0 tests the seam, not locks)."""
        return True, {}, []

    def axis_ids(self, context: MolecularContext) -> tuple[str, ...]:
        return (self._axis,)

    def suppression_for_target(self, parent: Any, target: Any, context: MolecularContext) -> Any:
        if self._representative is None:
            return None
        if str(target.target_id) == str(self._representative):
            return None
        return {
            "suppressed_target": str(target.target_id),
            "representative_target": str(self._representative),
            "orbit_id": "d0-orbit",
            "rho": 0.0,
            "site_action": {},
            "rotation": [],
            "pair_residual": 0.0,
            "witness_provenance": "d0-test",
            "stereo_centers_audited": [],
            "closure_order": 1,
        }


class _BatchStage(_ScriptStage):
    """Same script, plus the D0 retry hook (engine-dispatched only)."""

    def retry_solve(
        self, parent: Any, target: Any, context: Any, should_cancel: Any, first_pass: Any
    ) -> Any:
        self.retry_calls.append(str(target.target_id))
        self.retry_parents.append(str(parent.structure.id))
        if self.retry_probe is None:
            self.retry_probe = should_cancel
        self.first_pass_seen = first_pass
        mode = self._retry_by_slot.get(int(target.ordinal), self._retry_mode)
        if mode == "heal":
            # Indistinguishable from a first-try success by design (D0 adds
            # no provenance; D1 will carry retry_start). Replacement means
            # replacement: same structure id, same reason.
            return RealizationResult(
                structure=self._spawn(parent, str(target.target_id)),
                status="realized",
                reason="scripted-ok",
                backend="d0-script",
                evidence=(),
            )
        if mode == "fail_again":
            return RealizationResult(
                structure=None,
                status="numerical_failure",
                reason="scripted-retry-fail",
                backend="d0-script",
                evidence=(),
            )
        if mode == "raise_cancel":
            raise EngineCancelledError("d0 hook-raised cancel")
        return None


def _leaf_coords(run: Any) -> dict[str, tuple]:
    out = {}
    for leaf in run.leaves:
        out.setdefault(leaf.provenance["leaf_target_id"], leaf.structure.coordinates)
    return out


def test_decline_path_byte_equivalent() -> None:
    """Batch-decline run is byte-identical to the legacy path run."""
    legacy = _run(ConfgenEngine(stages=[_ScriptStage({"slots": 3, "fail_slots": (1,)})]), _ctx({}))
    batch_stage = _BatchStage({"slots": 3, "fail_slots": (1,)})
    batch = _run(ConfgenEngine(stages=[batch_stage]), _ctx({}))
    assert _sig(legacy) == _sig(batch)
    assert legacy.report_json() == batch.report_json()
    assert _leaf_coords(legacy) == _leaf_coords(batch)
    assert _terminals_by_target(batch) == {
        "rings:000000": ["published_leaf"],
        "rings:000001": ["failed_numerical"],
        "rings:000002": ["published_leaf"],
    }
    # Dispatch really happened: exactly the failed target visited the hook.
    assert batch_stage.retry_calls == ["rings:000001"]
    # No extra enumeration on the hook path (count pass + real pass only).
    assert batch_stage.enumerate_calls == 2


def test_root_counterexample_single_terminal_and_certificate() -> None:
    """Root repro: healed target has ONE terminal; certificate equations hold."""
    healer = _BatchStage({"slots": 3, "fail_slots": (1,), "retry_mode": "heal"})
    healed = _run(ConfgenEngine(stages=[healer]), _ctx({}))
    assert healer.retry_calls == ["rings:000001"]
    assert _terminals_by_target(healed) == {
        "rings:000000": ["published_leaf"],
        "rings:000001": ["published_leaf"],
        "rings:000002": ["published_leaf"],
    }
    records = list(healed.target_records)
    ok, _ = verify_count_equations(records, raw=3, sampled=3)
    assert ok
    tok, _ = verify_terminal_equations(records)
    assert tok
    report = healed.report_json()
    assert report["certificate"]["equations_ok"] is True
    assert report["counts"]["status_counts"] == {"published_leaf": 3}
    assert report["counts"]["failed"] == {}


def test_healed_equals_first_try_ideal() -> None:
    """Replacement means replacement: heal run is byte-identical to first-try success."""
    ideal = _run(ConfgenEngine(stages=[_BatchStage({"slots": 3})]), _ctx({}))
    healed = _run(
        ConfgenEngine(stages=[_BatchStage({"slots": 3, "fail_slots": (1,), "retry_mode": "heal"})]),
        _ctx({}),
    )
    assert healed.report_json() == ideal.report_json()
    assert _sig(healed) == _sig(ideal)
    assert _leaf_coords(healed) == _leaf_coords(ideal)


def test_healed_record_takes_original_position() -> None:
    """The replacement sits at the stale record's index; others keep order."""
    decline = _run(ConfgenEngine(stages=[_BatchStage({"slots": 3, "fail_slots": (1,)})]), _ctx({}))
    healed = _run(
        ConfgenEngine(stages=[_BatchStage({"slots": 3, "fail_slots": (1,), "retry_mode": "heal"})]),
        _ctx({}),
    )
    assert healed.target_records[1].target_id == "rings:000001"
    assert healed.target_records[1].status.value == "published_leaf"
    assert healed.target_records[0] == decline.target_records[0]
    assert healed.target_records[2] == decline.target_records[2]


def test_failed_retry_still_single_terminal() -> None:
    """A retry that fails again leaves exactly one (new) failure terminal."""
    stage = _BatchStage({"slots": 3, "fail_slots": (1,), "retry_mode": "fail_again"})
    run = _run(ConfgenEngine(stages=[stage]), _ctx({}))
    assert stage.retry_calls == ["rings:000001"]
    assert _terminals_by_target(run)["rings:000001"] == ["failed_numerical"]
    assert _sig(run)[1][2] == "scripted-retry-fail"
    records = list(run.target_records)
    ok, _ = verify_count_equations(records, raw=3, sampled=3)
    assert ok
    report = run.report_json()
    assert report["certificate"]["equations_ok"] is True
    assert report["counts"]["failed"] == {"failed_numerical": 1}


def test_legacy_cancel_recorded_as_stage_error() -> None:
    """Old behavior preserved: a default-path cancel is a stage_error record."""
    stage = _ScriptStage({"slots": 3, "raise_slots": {0: "cancel"}})
    run = _run(ConfgenEngine(stages=[stage]), _ctx({}))  # must NOT raise
    by_id = _terminals_by_target(run)
    assert by_id["rings:000000"] == ["failed_numerical"]
    assert _sig(run)[0][2] == "stage_error:EngineCancelledError"
    assert by_id["rings:000001"] == ["published_leaf"]


def test_cancel_in_retry_propagates_unwrapped() -> None:
    """Probe fire during the retry pass raises; never a stage_error record."""
    stage = _BatchStage({"slots": 3, "fail_slots": (1,), "retry_mode": "heal"})
    engine = ConfgenEngine(stages=[stage])
    calls: list[int] = []

    def _probe() -> bool:
        calls.append(1)
        return len(calls) > 3  # pass the 3 first-pass checks, fire in retry

    with pytest.raises(EngineCancelledError):
        _run(engine, _ctx({}), probe=_probe)
    assert stage.retry_calls == []  # check precedes any retry solve


def test_hook_receives_real_probe_and_hook_raise_propagates() -> None:
    """The hook gets the identical probe object; its cancel is not wrapped."""
    marker: list[bool] = []

    def _probe() -> bool:
        marker.append(True)
        return False

    stage = _BatchStage({"slots": 3, "fail_slots": (1,), "retry_mode": "heal"})
    _run(ConfgenEngine(stages=[stage]), _ctx({}), probe=_probe)
    assert stage.retry_probe is _probe

    raiser = _BatchStage({"slots": 2, "fail_slots": (0,), "retry_mode": "raise_cancel"})
    with pytest.raises(EngineCancelledError):
        _run(ConfgenEngine(stages=[raiser]), _ctx({}), probe=_probe)


def test_excluded_targets_never_solved() -> None:
    """Policy-excluded targets are solved in neither pass on either path."""
    spec = {
        "exclusions": [{"axis": "rings", "match": {"slot": 1}, "reason": "d0-test"}],
    }
    for cls in (_ScriptStage, _BatchStage):
        stage = cls({"slots": 3})
        run = _run(ConfgenEngine(stages=[stage]), _ctx(spec))
        assert "rings:000001" not in stage.solve_calls
        if isinstance(stage, _BatchStage):
            assert stage.retry_calls == []
        rejected = [sig for sig in _sig(run) if sig[0] == "rings:000001"]
        assert rejected and all(status == "rejected_by_policy" for _, status, _ in rejected)


def test_suppression_fallback_and_success_preserved() -> None:
    """Representative-failed fallback and success suppression hold on both paths."""
    # Fallback: representative failed -> candidate realizes normally.
    for cls in (_ScriptStage, _BatchStage):
        stage = cls({"slots": 2, "fail_slots": (0,), "representative": "rings:000000"})
        run = _run(ConfgenEngine(stages=[stage]), _ctx({}))
        by_id = {target_id: status for target_id, status, _ in _sig(run)}
        assert by_id["rings:000000"] == "failed_numerical"
        assert by_id["rings:000001"] == "published_leaf"
        assert sorted(stage.solve_calls) == ["rings:000000", "rings:000001"]
    # Success: representative published -> candidate suppressed, unsolved.
    for cls in (_ScriptStage, _BatchStage):
        stage = cls({"slots": 2, "representative": "rings:000000"})
        run = _run(ConfgenEngine(stages=[stage]), _ctx({}))
        by_id = {target_id: status for target_id, status, _ in _sig(run)}
        assert by_id["rings:000001"] == "suppressed_by_verified_symmetry"
        assert stage.solve_calls == ["rings:000000"]


def test_stage_error_evidence_never_retried() -> None:
    """Exception-origin failures are bugs, not science: the hook skips them."""
    stage = _BatchStage(
        {"slots": 3, "raise_slots": {0: "error"}, "fail_slots": (1,), "retry_mode": "heal"}
    )
    run = _run(ConfgenEngine(stages=[stage]), _ctx({}))
    assert stage.retry_calls == ["rings:000001"]  # slot0 stage_error skipped
    by_id = _terminals_by_target(run)
    assert by_id["rings:000000"] == ["failed_numerical"]
    assert by_id["rings:000001"] == ["published_leaf"]
    assert by_id["rings:000002"] == ["published_leaf"]


def test_drift_not_retried() -> None:
    """FAILED_DRIFT is an audit verdict, not a solve failure: no retry."""
    stage = _BatchStage({"slots": 2, "drift_slots": (0,), "retry_mode": "heal"})
    run = _run(ConfgenEngine(stages=[stage]), _ctx({}))
    assert stage.retry_calls == []
    by_id = _terminals_by_target(run)
    assert by_id["rings:000000"] == ["failed_drift"]
    assert by_id["rings:000001"] == ["published_leaf"]


def test_first_pass_table_immutable_and_complete() -> None:
    """The hook's table is an immutable per-parent snapshot with structures."""
    stage = _BatchStage({"slots": 3, "fail_slots": (1,)})
    _run(ConfgenEngine(stages=[stage]), _ctx({}))
    table = stage.first_pass_seen
    assert isinstance(table, tuple) and len(table) == 3
    assert all(isinstance(entry, RetryFirstPass) for entry in table)
    rows = {entry.ordinal: entry for entry in table}
    assert rows[0].status == "published_leaf" and rows[0].solver_error is False
    assert rows[1].status == "failed_numerical" and rows[1].structure is None
    assert rows[0].structure is not None
    assert (
        rows[0].structure.coordinates
        == _leaf_coords(_run(ConfgenEngine(stages=[_BatchStage({"slots": 3})]), _ctx({})))[
            "rings:000000"
        ]
    )
    with pytest.raises(TypeError):
        table[0] = table[1]  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        rows[0].status = "x"  # type: ignore[misc]


def test_two_level_combo_heal_revokes_deferred() -> None:
    """Non-last heal: stale deferred ranges gone, counters exact, others intact."""
    decline_low = _BatchStage({"axis": "rings", "slots": 2})
    decline = _run(
        ConfgenEngine(
            stages=[
                _BatchStage({"axis": "coordination", "slots": 2, "fail_slots": (0,)}),
                decline_low,
            ]
        ),
        _ctx({"seed": 11}, tag="d0c"),
    )
    assert any(
        r.status.value == "deferred_parent_failed" for r in decline.target_records
    ), "expected stale deferred in the decline run"

    heal_low = _BatchStage({"axis": "rings", "slots": 2})
    heal_top = _BatchStage(
        {"axis": "coordination", "slots": 2, "fail_slots": (0,), "retry_mode": "heal"}
    )
    healed = _run(ConfgenEngine(stages=[heal_top, heal_low]), _ctx({"seed": 11}, tag="d0c"))
    assert sorted(heal_top.retry_parents) == ["d0c"]
    assert len(healed.leaves) == 4
    assert not [r for r in healed.target_records if r.status.value == "deferred_parent_failed"]
    report = healed.report_json()
    assert report["counts"]["deferred_parent_failed_leaves"] == 0
    assert report["certificate"]["equations_ok"] is True
    ok, _ = verify_count_equations(list(healed.target_records), raw=4, sampled=4)
    assert ok
    tok, _ = verify_terminal_equations(list(healed.target_records))
    assert tok
    assert _terminals_by_target(healed)["coordination:000000"] == ["expanded"]
    # Untouched branch leaves stand byte-identical (same ids and coordinates).
    decline_structs = {(leaf.structure.id, leaf.structure.coordinates) for leaf in decline.leaves}
    healed_structs = {(leaf.structure.id, leaf.structure.coordinates) for leaf in healed.leaves}
    assert decline_structs <= healed_structs


def test_two_parents_isolated_and_repeatable() -> None:
    """Retries are per-parent; reruns with shared instances agree exactly."""
    top = _ScriptStage({"axis": "coordination", "slots": 2})
    low = _BatchStage({"axis": "rings", "slots": 2, "fail_slots": (0,), "retry_mode": "heal"})
    engine = ConfgenEngine(stages=[top, low])
    first = _run(engine, _ctx({"seed": 11}, tag="d0p"))
    assert len(low.retry_parents) == 2
    assert low.retry_parents[0] != low.retry_parents[1]  # one retry per parent
    assert len(first.leaves) == 4
    # Same target id under two parents: exactly one terminal each.
    assert _terminals_by_target(first)["rings:000000"] == ["published_leaf"] * 2
    second = _run(engine, _ctx({"seed": 11}, tag="d0p"))
    assert _sig(first) == _sig(second)
    assert first.report_json() == second.report_json()


def test_new_seam_has_no_axis_names_or_component_imports() -> None:
    """The real unified AST policy validates the kernel additions."""
    from pathlib import Path

    import tools.architecture_policy as policy

    root = Path(__file__).resolve().parents[2]
    assert policy.scan(root) == []


def test_root_multi_failure_second_retry_relocates() -> None:
    """Root blocker repro: two healed failures in one parent; 6 leaves, single terminals."""
    top = _BatchStage(
        {"axis": "coordination", "slots": 3, "fail_slots": (0, 1), "retry_mode": "heal"}
    )
    low = _BatchStage({"axis": "rings", "slots": 2})
    healed = _run(ConfgenEngine(stages=[top, low]), _ctx({"seed": 11}, tag="d0m"))
    assert sorted(top.retry_calls) == ["coordination:000000", "coordination:000001"]
    assert len(healed.leaves) == 6
    by_id = _terminals_by_target(healed)
    assert by_id["coordination:000000"] == ["expanded"]
    assert by_id["coordination:000001"] == ["expanded"]
    assert by_id["coordination:000002"] == ["expanded"]
    assert not [r for r in healed.target_records if r.status.value == "deferred_parent_failed"]
    report = healed.report_json()
    assert report["counts"]["deferred_parent_failed_leaves"] == 0
    assert report["certificate"]["equations_ok"] is True
    records = list(healed.target_records)
    ok, _ = verify_count_equations(records, raw=6, sampled=6)
    assert ok
    tok, _ = verify_terminal_equations(records)
    assert tok


def test_mixed_retry_modes_multi_target() -> None:
    """heal/fail_again/decline mixed across 3 failures: each keeps one terminal."""
    stage = _BatchStage(
        {
            "slots": 4,
            "fail_slots": (0, 1, 2),
            "retry_by_slot": {0: "heal", 1: "fail_again", 2: "decline"},
        }
    )
    run = _run(ConfgenEngine(stages=[stage]), _ctx({}))
    assert sorted(stage.retry_calls) == ["rings:000000", "rings:000001", "rings:000002"]
    by_id = _terminals_by_target(run)
    assert by_id["rings:000000"] == ["published_leaf"]
    assert by_id["rings:000001"] == ["failed_numerical"]
    assert by_id["rings:000002"] == ["failed_numerical"]
    assert by_id["rings:000003"] == ["published_leaf"]
    sig = {target_id: reason for target_id, _, reason in _sig(run)}
    assert sig["rings:000001"] == "scripted-retry-fail"
    assert sig["rings:000002"] == "scripted-fail"  # declined: original stands
    report = run.report_json()
    assert report["certificate"]["equations_ok"] is True
    assert report["counts"]["failed"] == {"failed_numerical": 2}
    records = list(run.target_records)
    ok, _ = verify_count_equations(records, raw=4, sampled=4)
    assert ok


def test_two_level_mixed_heal_and_failagain() -> None:
    """Two replacements in one parent: both relocate; deferred re-issued only for the new failure."""
    low = _BatchStage({"axis": "rings", "slots": 2})
    top = _BatchStage(
        {
            "axis": "coordination",
            "slots": 3,
            "fail_slots": (0, 1),
            "retry_by_slot": {0: "heal", 1: "fail_again"},
        }
    )
    run = _run(ConfgenEngine(stages=[top, low]), _ctx({"seed": 13}, tag="d0x"))
    assert sorted(top.retry_calls) == ["coordination:000000", "coordination:000001"]
    by_id = _terminals_by_target(run)
    assert by_id["coordination:000000"] == ["expanded"]
    assert by_id["coordination:000001"] == ["failed_numerical"]
    assert by_id["coordination:000002"] == ["expanded"]
    sig = {target_id: reason for target_id, _, reason in _sig(run)}
    assert sig["coordination:000001"] == "scripted-retry-fail"
    leftover = [r for r in run.target_records if r.status.value == "deferred_parent_failed"]
    assert leftover and all(r.parent_target_id == "coordination:000001" for r in leftover)
    assert len(run.leaves) == 4  # healed branch 2 + intact branch 2
    report = run.report_json()
    assert report["counts"]["deferred_parent_failed_leaves"] == 2
    assert report["certificate"]["equations_ok"] is True
    records = list(run.target_records)
    ok, _ = verify_count_equations(records, raw=6, sampled=6)
    assert ok
    tok, _ = verify_terminal_equations(records)
    assert tok


def test_two_level_decline_keeps_deferred() -> None:
    """A declined sibling keeps its failure and deferred subtree untouched."""
    low = _BatchStage({"axis": "rings", "slots": 2})
    top = _BatchStage(
        {
            "axis": "coordination",
            "slots": 3,
            "fail_slots": (0, 1),
            "retry_by_slot": {0: "heal", 1: "decline"},
        }
    )
    run = _run(ConfgenEngine(stages=[top, low]), _ctx({"seed": 13}, tag="d0y"))
    assert sorted(top.retry_calls) == ["coordination:000000", "coordination:000001"]
    by_id = _terminals_by_target(run)
    assert by_id["coordination:000000"] == ["expanded"]
    assert by_id["coordination:000001"] == ["failed_numerical"]
    sig = {target_id: reason for target_id, _, reason in _sig(run)}
    assert sig["coordination:000001"] == "scripted-fail"  # original stands
    leftover = [r for r in run.target_records if r.status.value == "deferred_parent_failed"]
    assert leftover and all(r.parent_target_id == "coordination:000001" for r in leftover)
    assert len(run.leaves) == 4
    report = run.report_json()
    assert report["counts"]["deferred_parent_failed_leaves"] == 2
    assert report["certificate"]["equations_ok"] is True
