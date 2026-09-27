#!/usr/bin/env python3

"""Crash-consistency publication protocol for V4.

The protocol fixes the only valid commit order for turning executed work items
into a published step result.  Persistence is not implemented in V4-1, but the
ordering is encoded as a state machine and as a durability check so future
storage layers and tests share one authority.

Frozen order:

1. work item execution finishes;
2. produced artifacts are fully written;
3. artifact checksums are verified;
4. the work item result is durably persisted;
5. accepted work item results are assembled;
6. the step result is atomically published;
7. the run state transitions the step to completed/partial;
8. downstream bindings become eligible.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import IntEnum

from .completion import StepStatus, WorkItemStatus
from .errors import InvalidResultError, PublicationError
from .result import find_duplicate_result_ids, require_production_ids
from .step_result import StepResult

__all__ = [
    "PUBLICATION_ORDER",
    "PublicationStage",
    "PublicationTracker",
    "verify_step_publication",
]


def _require_result_identity(step_result: StepResult) -> None:
    """Reject missing or duplicate production result ids before publication.

    Stamping is the emitters' job (profiles, analysis); gating is here, at
    the runtime boundary, so no caller can publish identity-less results
    by merely carrying the optional field.
    """
    try:
        require_production_ids(step_result.results)
        for item in step_result.item_results:
            require_production_ids(item.results)
    except InvalidResultError as exc:
        raise PublicationError(
            f"cannot publish step {step_result.step_id!r} with identity-less results: {exc}"
        ) from exc
    duplicates = find_duplicate_result_ids(step_result.results)
    if duplicates:
        raise PublicationError(
            f"cannot publish step {step_result.step_id!r} with duplicate result ids: "
            + ", ".join(duplicates)
        )


class PublicationStage(IntEnum):
    """Ordered stages of the V4 publication protocol."""

    ITEM_EXECUTED = 1
    ARTIFACTS_WRITTEN = 2
    CHECKSUMS_VERIFIED = 3
    ITEM_RESULT_DURABLE = 4
    STEP_ASSEMBLED = 5
    STEP_RESULT_PUBLISHED = 6
    RUN_STATE_UPDATED = 7
    DOWNSTREAM_ELIGIBLE = 8


#: The frozen publication order; every stage must be reached exactly in turn.
PUBLICATION_ORDER: tuple[PublicationStage, ...] = tuple(PublicationStage)


class PublicationTracker:
    """A monotone state machine over :class:`PublicationStage`.

    Raises
    ------
    PublicationError
        Raised when a stage is skipped, repeated, or out of order.
    """

    __slots__ = ("_stage",)

    def __init__(self) -> None:
        self._stage: PublicationStage | None = None

    @property
    def stage(self) -> PublicationStage | None:
        """Return the last successfully reached stage."""
        return self._stage

    def advance(self, stage: PublicationStage) -> None:
        """Advance to the next stage in the frozen order."""
        if not isinstance(stage, PublicationStage):
            raise PublicationError("stage must be a PublicationStage")
        if self._stage is None:
            expected: PublicationStage | None = PUBLICATION_ORDER[0]
        else:
            index = PUBLICATION_ORDER.index(self._stage)
            expected = PUBLICATION_ORDER[index + 1] if index + 1 < len(PUBLICATION_ORDER) else None
        if expected is None:
            raise PublicationError("publication already complete; no further stages exist")
        if stage is not expected:
            raise PublicationError(
                f"publication out of order: expected {expected.name}, got {stage.name}"
            )
        self._stage = stage

    def reached(self, stage: PublicationStage) -> bool:
        """Return whether *stage* has already been reached."""
        if self._stage is None:
            return False
        return self._stage.value >= stage.value

    def require_complete(self) -> None:
        """Raise unless every stage has been reached."""
        if self._stage is not PUBLICATION_ORDER[-1]:
            raise PublicationError("publication protocol did not complete")


def verify_step_publication(
    step_result: StepResult,
    *,
    durable_item_ids: Iterable[str],
    verified_artifact_checksums: Iterable[str],
) -> None:
    """Verify a step result may be published durably.

    A step result may only be published when every backing work-item result is
    durable and every published artifact has a verified checksum.  This is the
    invariant that prevents "state says completed" while results are missing.

    Parameters
    ----------
    step_result : StepResult
        Result about to be published.
    durable_item_ids : Iterable[str]
        Work-item ids whose results are durably persisted.
    verified_artifact_checksums : Iterable[str]
        Checksums that have been verified against written bytes.

    Raises
    ------
    PublicationError
        Raised when any durability requirement is unmet, or when any
        published result lacks a producer-scoped identity or collides on
        one (legacy ``result_id=None`` records never become production
        refs silently).
    """
    _require_result_identity(step_result)
    durable = set(durable_item_ids)
    # Carried CANCELLED items are terminal lifecycle reports without a
    # scientific payload; they never counterfeit coverage and are exempt
    # from the durability requirement (mirrors the batch gap gate).
    cancelled_ids = {
        item.work_item_id
        for item in step_result.item_results
        if item.status is WorkItemStatus.CANCELLED
    }
    missing_items = [
        item_id
        for item_id in step_result.work_item_ids
        if item_id not in durable and item_id not in cancelled_ids
    ]
    if missing_items:
        raise PublicationError(
            "cannot publish step result before work item results are durable: "
            + ", ".join(sorted(missing_items))
        )
    verified = {checksum.lower() for checksum in verified_artifact_checksums}
    for artifact in step_result.artifacts:
        if artifact.checksum is None:
            raise PublicationError(
                f"published artifact {artifact.id!r} has no checksum; "
                "content verification is required before publication"
            )
        if artifact.checksum.lower() not in verified:
            raise PublicationError(f"published artifact {artifact.id!r} checksum is not verified")
    if step_result.status is StepStatus.COMPLETED:
        for item in step_result.item_results:
            if not item.is_completed:
                raise PublicationError(
                    f"step is marked completed but item {item.work_item_id!r} is not"
                )
