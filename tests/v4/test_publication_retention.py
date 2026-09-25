#!/usr/bin/env python3

"""Publication protocol and retention tests for the V4 domain.

Pins the frozen eight-stage commit order, the durability/check-sum gate that
protects published step results, and the retention-class GC matrix.
"""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from confflow.domain import (
    PUBLICATION_ORDER,
    ArtifactLocator,
    ArtifactRef,
    ArtifactSet,
    PublicationError,
    PublicationStage,
    PublicationTracker,
    RetentionClass,
    StepResult,
    StepStatus,
    WorkItemResult,
    WorkItemStatus,
    may_garbage_collect,
    verify_step_publication,
)
from tests.v4._builders import checkpoint

EXPECTED_STAGES = (
    PublicationStage.ITEM_EXECUTED,
    PublicationStage.ARTIFACTS_WRITTEN,
    PublicationStage.CHECKSUMS_VERIFIED,
    PublicationStage.ITEM_RESULT_DURABLE,
    PublicationStage.STEP_ASSEMBLED,
    PublicationStage.STEP_RESULT_PUBLISHED,
    PublicationStage.RUN_STATE_UPDATED,
    PublicationStage.DOWNSTREAM_ELIGIBLE,
)


def completed_item(item_id: str = "wi:s1:a") -> WorkItemResult:
    """Return a completed work-item result."""
    return WorkItemResult(work_item_id=item_id, status=WorkItemStatus.COMPLETED)


def published_result(artifacts: Iterable[ArtifactRef] = ()) -> StepResult:
    """Return a completed step result for publication checks."""
    return StepResult(
        step_id="s1",
        status=StepStatus.COMPLETED,
        item_results=(completed_item(),),
        artifacts=ArtifactSet.of(*artifacts),
    )


def test_publication_order_is_the_frozen_eight_stage_sequence() -> None:
    """The commit order is exactly the documented stage sequence."""
    assert len(PUBLICATION_ORDER) == 8
    assert PUBLICATION_ORDER == EXPECTED_STAGES
    assert PUBLICATION_ORDER == tuple(PublicationStage)
    assert [stage.value for stage in PUBLICATION_ORDER] == list(range(1, 9))


def test_tracker_rejects_skipped_stage() -> None:
    """Stages must be reached in order, without gaps."""
    tracker = PublicationTracker()
    with pytest.raises(PublicationError, match="expected ITEM_EXECUTED"):
        tracker.advance(PublicationStage.ARTIFACTS_WRITTEN)
    assert tracker.stage is None


def test_tracker_rejects_repeated_stage() -> None:
    """A reached stage cannot be advanced to again."""
    tracker = PublicationTracker()
    tracker.advance(PublicationStage.ITEM_EXECUTED)
    with pytest.raises(PublicationError, match="expected ARTIFACTS_WRITTEN"):
        tracker.advance(PublicationStage.ITEM_EXECUTED)


def test_tracker_reached_and_require_complete() -> None:
    """reached() is monotone; require_complete() waits for the last stage."""
    tracker = PublicationTracker()
    assert tracker.reached(PublicationStage.ITEM_EXECUTED) is False
    with pytest.raises(PublicationError, match="did not complete"):
        tracker.require_complete()
    for stage in PUBLICATION_ORDER:
        tracker.advance(stage)
    assert tracker.stage is PublicationStage.DOWNSTREAM_ELIGIBLE
    assert tracker.reached(PublicationStage.ITEM_EXECUTED) is True
    assert tracker.reached(PublicationStage.STEP_ASSEMBLED) is True
    tracker.require_complete()
    with pytest.raises(PublicationError, match="already complete"):
        tracker.advance(PublicationStage.DOWNSTREAM_ELIGIBLE)


def test_verify_requires_durable_item_results() -> None:
    """Publication is blocked while a backing item result is not durable."""
    result = published_result()
    with pytest.raises(PublicationError, match="durable"):
        verify_step_publication(
            result,
            durable_item_ids=(),
            verified_artifact_checksums=(),
        )


def test_verify_requires_artifact_checksum() -> None:
    """An unverified artifact without a checksum blocks publication."""
    artifact = ArtifactRef(
        id="chk_a",
        role="checkpoint",
        locator=ArtifactLocator.run_relative("steps/s1/a.chk"),
    )
    result = published_result([artifact])
    with pytest.raises(PublicationError, match="has no checksum"):
        verify_step_publication(
            result,
            durable_item_ids=("wi:s1:a",),
            verified_artifact_checksums=(),
        )


def test_verify_requires_verified_checksum() -> None:
    """A recorded checksum must also appear in the verified set."""
    artifact = checkpoint("deadbeef", artifact_id="chk_a", producer_step_id="s1")
    result = published_result([artifact])
    with pytest.raises(PublicationError, match="not verified"):
        verify_step_publication(
            result,
            durable_item_ids=("wi:s1:a",),
            verified_artifact_checksums=("sha256:" + "0" * 64,),
        )


def test_verify_accepts_consistent_publication() -> None:
    """Durable items and verified checksums unlock publication."""
    artifact = checkpoint("deadbeef", artifact_id="chk_a", producer_step_id="s1")
    result = published_result([artifact])
    assert artifact.checksum is not None
    verify_step_publication(
        result,
        durable_item_ids=("wi:s1:a",),
        verified_artifact_checksums=(artifact.checksum,),
    )
    verify_step_publication(
        result,
        durable_item_ids=("wi:s1:a",),
        verified_artifact_checksums=(artifact.checksum.upper(),),
    )


@pytest.mark.parametrize(
    ("retention", "consumed", "run_complete", "expected"),
    [
        pytest.param(RetentionClass.RETAINED, True, True, False, id="retained-consumed"),
        pytest.param(RetentionClass.RETAINED, False, False, False, id="retained-unconsumed"),
        pytest.param(
            RetentionClass.INTERMEDIATE,
            True,
            False,
            False,
            id="intermediate-run-open",
        ),
        pytest.param(
            RetentionClass.INTERMEDIATE,
            False,
            True,
            False,
            id="intermediate-unconsumed",
        ),
        pytest.param(
            RetentionClass.INTERMEDIATE,
            True,
            True,
            True,
            id="intermediate-consumed-run-complete",
        ),
        pytest.param(RetentionClass.TEMPORARY, False, False, False, id="temporary-unconsumed"),
        pytest.param(RetentionClass.TEMPORARY, True, False, True, id="temporary-consumed"),
        pytest.param(RetentionClass.TEMPORARY, True, True, True, id="temporary-consumed-complete"),
    ],
)
def test_retention_gc_matrix(
    retention: RetentionClass,
    consumed: bool,
    run_complete: bool,
    expected: bool,
) -> None:
    """GC eligibility depends on class plus consumption and run state."""
    assert (
        may_garbage_collect(
            retention,
            referenced=False,
            consumed=consumed,
            run_complete=run_complete,
        )
        is expected
    )


@pytest.mark.parametrize("retention", list(RetentionClass))
def test_referenced_artifacts_are_never_collected(retention: RetentionClass) -> None:
    """Published references outrank any declared retention class."""
    assert (
        may_garbage_collect(
            retention,
            referenced=True,
            consumed=True,
            run_complete=True,
        )
        is False
    )
