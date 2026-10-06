#!/usr/bin/env python3
"""FIX1D kernel/wire/accounting boundaries: real fail-closed validation.

Self-contained. Covers only newly added FIX1D semantics that are currently
below the global gate: TelemetryRow / BoundTelemetryEvent / RetryResult
illegal params (TelemetryError, never silent), wire unknown-component
fail-closed + legacy type guards, accounting suppressed-after-issue truth,
and GenerationStage retry-hook defaults. No production logic touched.
"""

from __future__ import annotations

import dataclasses

import pytest

from confflow.domain._immutable import FrozenDict
from confflow.science.confgen import ConfgenEngine
from confflow.science.confgen.accounting import (
    TargetRecord,
    TerminalStatus,
    attempt_ledger_counts,
)
from confflow.science.confgen.kernel_records import (
    BoundTelemetryEvent,
    ComponentStateKey,
    RetryResult,
    TelemetryError,
    TelemetryRow,
)
from confflow.science.confgen.model import (
    RETRY_DEFAULT_PHASE,
    GenerationStage,
    RealizationResult,
)


def _ok_outcome() -> RealizationResult:
    return RealizationResult(
        structure=None,
        status="numerical_failure",
        reason="probe",
        backend="probe",
        evidence=(),
    )


# --- TelemetryRow illegal params -------------------------------------------


def test_telemetry_row_rejects_empty_and_nonstring_kind() -> None:
    with pytest.raises(TelemetryError):
        TelemetryRow(kind="", attempts=1, solve_successes=0, diagnostic={})
    with pytest.raises(TelemetryError):
        TelemetryRow(kind=123, attempts=1, solve_successes=0, diagnostic={})  # type: ignore[arg-type]


def test_telemetry_row_rejects_bool_negative_and_nonint_counts() -> None:
    with pytest.raises(TelemetryError):
        TelemetryRow(kind="k", attempts=True, solve_successes=0, diagnostic={})
    with pytest.raises(TelemetryError):
        TelemetryRow(kind="k", attempts=-1, solve_successes=0, diagnostic={})
    with pytest.raises(TelemetryError):
        TelemetryRow(kind="k", attempts=1.5, solve_successes=0, diagnostic={})  # type: ignore[arg-type]
    with pytest.raises(TelemetryError):
        TelemetryRow(kind="k", attempts=1, solve_successes=True, diagnostic={})
    with pytest.raises(TelemetryError):
        TelemetryRow(kind="k", attempts=1, solve_successes=-2, diagnostic={})


def test_telemetry_row_rejects_success_exceeding_attempts() -> None:
    with pytest.raises(TelemetryError):
        TelemetryRow(kind="k", attempts=1, solve_successes=2, diagnostic={})


def test_telemetry_row_rejects_non_json_diagnostic_and_freezes() -> None:
    with pytest.raises(TelemetryError):
        TelemetryRow(kind="k", attempts=1, solve_successes=0, diagnostic={"x": object()})
    with pytest.raises(TelemetryError):
        TelemetryRow(kind="k", attempts=1, solve_successes=0, diagnostic={1: "v"})  # type: ignore[dict-item]
    with pytest.raises(TelemetryError):
        TelemetryRow(kind="k", attempts=1, solve_successes=0, diagnostic={"f": float("nan")})
    row = TelemetryRow(kind="k", attempts=2, solve_successes=1, diagnostic={"a": [1, 2]})
    assert isinstance(row.diagnostic, FrozenDict)
    with pytest.raises(dataclasses.FrozenInstanceError):
        row.attempts = 9  # type: ignore[misc]


# --- BoundTelemetryEvent illegal params ------------------------------------


def test_bound_event_rejects_bad_identity() -> None:
    good = dict(
        component_id="rings",
        parent_target_id=None,
        target_id="rings:000000",
        ordinal=0,
        phase="default",
        kind="k",
    )
    with pytest.raises(TelemetryError):
        BoundTelemetryEvent(**{**good, "component_id": ""})
    with pytest.raises(TelemetryError):
        BoundTelemetryEvent(**{**good, "parent_target_id": ""})
    with pytest.raises(TelemetryError):
        BoundTelemetryEvent(**{**good, "parent_target_id": 7})  # type: ignore[arg-type]
    with pytest.raises(TelemetryError):
        BoundTelemetryEvent(**{**good, "target_id": ""})
    with pytest.raises(TelemetryError):
        BoundTelemetryEvent(**{**good, "ordinal": True})
    with pytest.raises(TelemetryError):
        BoundTelemetryEvent(**{**good, "ordinal": -1})
    with pytest.raises(TelemetryError):
        BoundTelemetryEvent(**{**good, "phase": ""})
    with pytest.raises(TelemetryError):
        BoundTelemetryEvent(**{**good, "kind": ""})


def test_bound_event_rejects_bad_counts_accepted_and_diagnostic() -> None:
    good = dict(
        component_id="rings",
        parent_target_id="p",
        target_id="rings:000001",
        ordinal=1,
        phase="default",
        kind="k",
    )
    with pytest.raises(TelemetryError):
        BoundTelemetryEvent(**{**good, "attempts": True})
    with pytest.raises(TelemetryError):
        BoundTelemetryEvent(**{**good, "solve_successes": 3, "attempts": 2})
    with pytest.raises(TelemetryError):
        BoundTelemetryEvent(**{**good, "accepted": "yes"})  # type: ignore[arg-type]
    with pytest.raises(TelemetryError):
        BoundTelemetryEvent(**{**good, "diagnostic": {"x": object()}})
    accepted = BoundTelemetryEvent(**{**good, "accepted": True})
    declined = BoundTelemetryEvent(**{**good, "accepted": False})
    assert accepted.accepted is True and declined.accepted is False
    assert accepted.solve_successes == declined.solve_successes == 0


# --- RetryResult illegal params --------------------------------------------


def test_retry_result_rejects_wrong_outcome_type() -> None:
    row = TelemetryRow(kind="k", attempts=1, solve_successes=0, diagnostic={})
    with pytest.raises(TelemetryError):
        RetryResult(outcome="not-an-outcome", telemetry=(row,))  # type: ignore[arg-type]


def test_retry_result_rejects_bad_telemetry_container_and_entries() -> None:
    with pytest.raises(TelemetryError):
        RetryResult(outcome=None, telemetry={"k": 1})  # type: ignore[arg-type]
    with pytest.raises(TelemetryError):
        RetryResult(outcome=None, telemetry=("not-a-row",))  # type: ignore[arg-type]


def test_retry_result_rejects_bad_success_index() -> None:
    row = TelemetryRow(kind="k", attempts=1, solve_successes=0, diagnostic={})
    good_outcome = _ok_outcome()
    with pytest.raises(TelemetryError):
        RetryResult(outcome=good_outcome, telemetry=(row,), success_index=True)  # type: ignore[arg-type]
    with pytest.raises(TelemetryError):
        RetryResult(outcome=good_outcome, telemetry=(row,), success_index="0")  # type: ignore[arg-type]
    with pytest.raises(TelemetryError):
        RetryResult(outcome=good_outcome, telemetry=(row,), success_index=-1)
    with pytest.raises(TelemetryError):
        RetryResult(outcome=good_outcome, telemetry=(row,), success_index=5)
    with pytest.raises(TelemetryError):
        RetryResult(outcome=None, telemetry=(row,), success_index=0)


def test_retry_result_rejects_zero_attempt_diagnostic_selection() -> None:
    row = TelemetryRow(kind="k", attempts=1, solve_successes=0, diagnostic={})
    diag = TelemetryRow(kind="diag", attempts=0, solve_successes=0, diagnostic={})
    good_outcome = _ok_outcome()
    with pytest.raises(TelemetryError):
        RetryResult(outcome=good_outcome, telemetry=(row, diag), success_index=1)
    zero_success = TelemetryRow(kind="k", attempts=2, solve_successes=0, diagnostic={})
    with pytest.raises(TelemetryError):
        RetryResult(outcome=good_outcome, telemetry=(zero_success,), success_index=0)


def test_retry_result_list_telemetry_frozen_and_outcome_none_keeps_rows() -> None:
    row = TelemetryRow(kind="k", attempts=1, solve_successes=0, diagnostic={})
    wrapped = RetryResult(outcome=None, telemetry=[row])  # list accepted, frozen
    assert isinstance(wrapped.telemetry, tuple) and wrapped.telemetry == (row,)
    with pytest.raises(dataclasses.FrozenInstanceError):
        wrapped.telemetry = ()  # type: ignore[misc]


# --- wire fail-closed -------------------------------------------------------


def test_wire_unknown_component_and_type_guards_fail_closed() -> None:
    from confflow.science.confgen.wire_v3 import (
        UnsupportedWireComponent,
        from_wire_key,
        to_legacy_realization,
        to_legacy_target,
        to_wire_key,
    )

    with pytest.raises(UnsupportedWireComponent) as caught:
        to_wire_key(ComponentStateKey(components={"nope": {"x": 1}}))
    assert "v3 wire only carries" in str(caught.value)
    with pytest.raises(ValueError):
        from_wire_key(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        to_wire_key(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        to_legacy_realization(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        to_legacy_target(object())  # type: ignore[arg-type]
    assert ConfgenEngine is not None


def test_wire_assemble_scope_ignores_unknown_ids_without_drift() -> None:
    from confflow.science.confgen.wire_v3 import assemble_inherited_scope

    scope = assemble_inherited_scope(
        {"torsions": {"t": 1}, "rings": {"r": 2}, "coordination": {"c": 3}, "dummy": {"z": 9}}
    )
    assert set(scope) == {"torsions", "rings", "coordination"}
    assert scope["torsions"] == {"t": 1} and scope["rings"] == {"r": 2}
    assert scope["coordination"] == {"c": 3}
    empty = assemble_inherited_scope({})
    assert empty == {"torsions": {}, "rings": {}, "coordination": None}


def test_legacy_adapter_report_statistics_forwarding_is_truthful() -> None:
    from confflow.science.confgen.wire_v3 import LegacyStageAdapter

    class _Plain:
        axis = "rings"

    class _WithStats:
        axis = "rings"

        def report_statistics(self, snapshot):  # type: ignore[no-untyped-def]
            return {"events": len(tuple(snapshot))}

    assert LegacyStageAdapter(_Plain()).report_statistics(()) is None
    assert LegacyStageAdapter(_WithStats()).report_statistics((1, 2)) == {"events": 2}


# --- accounting truthful ----------------------------------------------------


def _record(target_id: str, axis: str, ordinal: int, status: TerminalStatus) -> TargetRecord:
    return TargetRecord(
        target_id=target_id,
        axis=axis,
        ordinal=ordinal,
        state_value={},
        status=status,
        reason="probe",
        parent_target_id=f"parent:{ordinal}",
    )


def test_accounting_suppressed_after_issue_truthful_and_legacy_skipped() -> None:
    suppressed = _record("rings:000001", "rings", 1, TerminalStatus.SUPPRESSED_BY_VERIFIED_SYMMETRY)
    legacy = attempt_ledger_counts([suppressed])
    assert legacy["suppressed_skipped"] == 1
    assert legacy["issued_attempts"] == 0
    assert "suppressed_after_issue" not in legacy
    history = [("parent:1", "rings", 1)]
    truthful = attempt_ledger_counts([suppressed], issued_history=history)
    assert truthful["suppressed_after_issue"] == 1
    assert truthful["issued_attempts"] == 1
    assert truthful["attempted_without_success"] == 1
    assert truthful["by_status"][TerminalStatus.SUPPRESSED_BY_VERIFIED_SYMMETRY.value] == 1
    # Set input and non-matching history stay skipped, never silently issued.
    as_set = attempt_ledger_counts([suppressed], issued_history=set(history))
    assert as_set["suppressed_after_issue"] == 1
    other = attempt_ledger_counts([suppressed], issued_history=[("parent:9", "rings", 9)])
    assert other["suppressed_skipped"] == 1 and "suppressed_after_issue" not in other


def test_accounting_deferred_policy_and_inexact_fail_closed() -> None:
    deferred = _record("rings:range:0-2", "rings", 0, TerminalStatus.DEFERRED_PARENT_FAILED)
    object.__setattr__(
        deferred,
        "state_value",
        __import__("confflow.domain._immutable", fromlist=["FrozenDict"]).FrozenDict(
            {"range_start": 0, "range_end": 2}
        ),
    )
    policy = _record("rings:000003", "rings", 3, TerminalStatus.REJECTED_BY_POLICY)
    counts = attempt_ledger_counts([deferred, policy])
    assert counts["deferred_unissued"] == 3
    assert counts["policy_screened"] == 1
    assert counts["issued_attempts"] == 0
    inexact = _record("rings:000004", "rings", 4, TerminalStatus.FAILED_NUMERICAL)
    object.__setattr__(
        inexact,
        "state_value",
        __import__("confflow.domain._immutable", fromlist=["FrozenDict"]).FrozenDict(
            {"estimated_leaves": 5}
        ),
    )
    with pytest.raises(ValueError):
        attempt_ledger_counts([inexact])


# --- GenerationStage retry defaults ----------------------------------------


def test_retry_hook_defaults_decline_and_delegate_truthfully() -> None:
    assert RETRY_DEFAULT_PHASE == "default"

    class _Minimal(GenerationStage):
        def __init__(self) -> None:
            pass

        @property
        def axis(self) -> str:
            return "rings"

        def estimate(self, parent, context):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        def enumerate_targets(self, parent, context):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        def realize(self, parent, target, context):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        def perceive(self, structure, context):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    stage = _Minimal()
    assert stage.report_statistics(()) is None
    assert stage.retry_solve(None, None, None, None, ()) is None  # type: ignore[arg-type]
    assert stage.retry_phases() is None
    assert stage.retry_solve_phase(None, None, None, None, (), "other", ()) is None  # type: ignore[arg-type]
