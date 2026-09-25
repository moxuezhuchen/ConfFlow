#!/usr/bin/env python3

"""Completion, failure-policy, and work-item outcome tests for V4.

Pins the separation between acceptance policy (step status) and scheduler
behavior (OnFailure), and the status/error consistency rules for work items.
"""

from __future__ import annotations

import dataclasses

import pytest

from confflow.domain import (
    CompletionMode,
    CompletionPolicy,
    Diagnostic,
    DiagnosticSeverity,
    InvalidCompletionPolicyError,
    InvalidWorkItemError,
    OnFailure,
    PartialOutputPolicy,
    ResourceRequest,
    ResultError,
    SchedulerPolicy,
    StepStatus,
    Timing,
    WorkItemResult,
    WorkItemStatus,
    evaluate_step_status,
)

ALLOW_PARTIAL_ONE = CompletionPolicy(mode=CompletionMode.ALLOW_PARTIAL, minimum_success=1)


def test_minimum_success_is_invalid_with_require_all() -> None:
    """``minimum_success`` only exists for allow_partial."""
    with pytest.raises(InvalidCompletionPolicyError, match="allow_partial"):
        CompletionPolicy(mode=CompletionMode.REQUIRE_ALL, minimum_success=1)


@pytest.mark.parametrize(
    "minimum_success",
    [
        pytest.param(0, id="zero"),
        pytest.param(-1, id="negative"),
        pytest.param(True, id="bool"),
        pytest.param(2.5, id="float"),
    ],
)
def test_minimum_success_must_be_a_positive_integer(minimum_success: object) -> None:
    """A zero or non-integer minimum is rejected."""
    with pytest.raises(InvalidCompletionPolicyError):
        CompletionPolicy(
            mode=CompletionMode.ALLOW_PARTIAL,
            minimum_success=minimum_success,  # type: ignore[arg-type]
        )


def test_allow_partial_accepts_explicit_minimum() -> None:
    """An allow_partial policy with minimum 3 is legal."""
    policy = CompletionPolicy(mode=CompletionMode.ALLOW_PARTIAL, minimum_success=3)
    assert policy.accepts_partial is True
    assert policy.effective_minimum_success() == 3
    assert policy.to_dict() == {
        "mode": "allow_partial",
        "minimum_success": 3,
        "partial_output": "deny",
    }


@pytest.mark.parametrize(
    ("item_statuses", "expected"),
    [
        pytest.param((), StepStatus.COMPLETED, id="empty"),
        pytest.param(
            (WorkItemStatus.COMPLETED, WorkItemStatus.COMPLETED),
            StepStatus.COMPLETED,
            id="all-completed",
        ),
    ],
)
def test_require_all_evaluation(
    item_statuses: tuple[WorkItemStatus, ...], expected: StepStatus
) -> None:
    """Vacuously complete and fully complete steps are completed."""
    policy = CompletionPolicy(mode=CompletionMode.REQUIRE_ALL)
    assert evaluate_step_status(policy, item_statuses) is expected


def test_require_all_with_one_failure_is_failed() -> None:
    """A single failure fails a require_all step."""
    statuses = (WorkItemStatus.COMPLETED,) * 19 + (WorkItemStatus.FAILED,)
    assert evaluate_step_status(CompletionPolicy(), statuses) is StepStatus.FAILED


def test_allow_partial_with_enough_success_is_partial() -> None:
    """Reaching the minimum success count accepts a partial step."""
    statuses = (WorkItemStatus.COMPLETED,) * 19 + (WorkItemStatus.FAILED,)
    assert evaluate_step_status(ALLOW_PARTIAL_ONE, statuses) is StepStatus.PARTIAL


def test_allow_partial_with_unreachable_minimum_is_failed() -> None:
    """A minimum above the completed count fails the step."""
    policy = CompletionPolicy(mode=CompletionMode.ALLOW_PARTIAL, minimum_success=20)
    statuses = (WorkItemStatus.COMPLETED,) * 19 + (WorkItemStatus.FAILED,)
    assert evaluate_step_status(policy, statuses) is StepStatus.FAILED


def test_cancellation_outranks_failures() -> None:
    """Operator cancellation wins over any failure mix."""
    assert (
        evaluate_step_status(ALLOW_PARTIAL_ONE, (WorkItemStatus.CANCELLED,)) is StepStatus.CANCELLED
    )
    assert (
        evaluate_step_status(
            ALLOW_PARTIAL_ONE,
            (WorkItemStatus.CANCELLED, WorkItemStatus.FAILED),
        )
        is StepStatus.CANCELLED
    )
    cancelled_with_success = (
        WorkItemStatus.COMPLETED,
        WorkItemStatus.CANCELLED,
    )
    assert evaluate_step_status(ALLOW_PARTIAL_ONE, cancelled_with_success) is (StepStatus.CANCELLED)


def test_partial_output_defaults_to_deny() -> None:
    """Partial producers require an explicit opt-in to be consumed."""
    policy = CompletionPolicy()
    assert policy.mode is CompletionMode.REQUIRE_ALL
    assert policy.partial_output is PartialOutputPolicy.DENY
    assert policy.to_dict()["partial_output"] == "deny"


def test_scheduler_policy_is_separate_from_completion_policy() -> None:
    """OnFailure is scheduler vocabulary, never an acceptance field."""
    completion_fields = {field.name for field in dataclasses.fields(CompletionPolicy)}
    assert completion_fields == {"mode", "minimum_success", "partial_output"}
    assert OnFailure.FAIL_FAST.value == "fail_fast"
    assert [member.value for member in OnFailure] == ["continue", "fail_fast"]
    assert not hasattr(CompletionPolicy(), "on_failure")
    scheduler = SchedulerPolicy(max_parallel_items=4, on_failure=OnFailure.FAIL_FAST)
    policy = CompletionPolicy(mode=CompletionMode.ALLOW_PARTIAL, minimum_success=2)
    assert scheduler.to_dict() == {"max_parallel_items": 4, "on_failure": "fail_fast"}
    assert policy.accepts_partial is True


def test_failed_work_item_requires_error_information() -> None:
    """A failed item without an error or error diagnostic is impossible."""
    with pytest.raises(InvalidWorkItemError, match="requires an error"):
        WorkItemResult(work_item_id="wi:s1:a", status=WorkItemStatus.FAILED)


def test_failed_work_item_accepts_structured_error() -> None:
    """A ResultError alone satisfies the failure contract."""
    result = WorkItemResult(
        work_item_id="wi:s1:a",
        status=WorkItemStatus.FAILED,
        error=ResultError(code="native_failure", message="program exited 1"),
    )
    assert result.status is WorkItemStatus.FAILED
    assert result.is_completed is False


def test_failed_work_item_accepts_error_diagnostic() -> None:
    """An ERROR diagnostic alone also satisfies the failure contract."""
    diagnostic = Diagnostic(
        code="native_failure",
        message="program crashed",
        severity=DiagnosticSeverity.ERROR,
    )
    result = WorkItemResult(
        work_item_id="wi:s1:a",
        status=WorkItemStatus.FAILED,
        diagnostics=(diagnostic,),
    )
    assert result.status is WorkItemStatus.FAILED


def test_failed_work_item_rejects_warning_only_diagnostics() -> None:
    """A warning is not an acceptable failure record."""
    diagnostic = Diagnostic(
        code="native_warning",
        message="program reported a warning",
        severity=DiagnosticSeverity.WARNING,
    )
    with pytest.raises(InvalidWorkItemError, match="requires an error"):
        WorkItemResult(
            work_item_id="wi:s1:a",
            status=WorkItemStatus.FAILED,
            diagnostics=(diagnostic,),
        )


def test_completed_work_item_rejects_error() -> None:
    """A completed item cannot carry error information."""
    with pytest.raises(InvalidWorkItemError, match="must not carry an error"):
        WorkItemResult(
            work_item_id="wi:s1:a",
            status=WorkItemStatus.COMPLETED,
            error=ResultError(code="late_failure", message="should not exist"),
        )


def test_timing_rejects_finished_before_started() -> None:
    """Wall-clock ordering is validated; negative duration alone is allowed."""
    with pytest.raises(InvalidWorkItemError, match="must not precede"):
        Timing(started_at=10.0, finished_at=5.0)
    assert Timing(started_at=10.0, finished_at=10.0).finished_at == 10.0
    assert Timing(duration_seconds=-1.0).duration_seconds == -1.0


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="infinite"),
        pytest.param(True, id="bool"),
        pytest.param("10", id="string"),
    ],
)
def test_timing_rejects_non_finite_and_non_numeric_values(value: object) -> None:
    """Timing values must be finite numbers or None."""
    with pytest.raises(InvalidWorkItemError, match="timing"):
        Timing(started_at=value)  # type: ignore[arg-type]


def test_work_item_result_defaults_are_empty() -> None:
    """A completed item with no outputs is valid and carries no error."""
    result = WorkItemResult(work_item_id="wi:s1:a", status=WorkItemStatus.COMPLETED)
    assert result.is_completed is True
    assert result.error is None
    assert len(result.structures) == 0
    assert len(result.results) == 0
    assert len(result.artifacts) == 0
    assert result.to_dict()["status"] == "completed"
    assert result.semantic_digest is None


def test_resources_are_independent_of_failure_policy() -> None:
    """Constructing a fully resolved request needs no completion policy."""
    request = ResourceRequest(cores_per_item=4, memory_per_item_bytes=1024**3)
    policy = CompletionPolicy(mode=CompletionMode.ALLOW_PARTIAL, minimum_success=1)
    assert request.is_resolved is True
    assert policy.accepts_partial is True
