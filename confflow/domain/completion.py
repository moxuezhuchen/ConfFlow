#!/usr/bin/env python3

"""V4 completion and failure semantics.

Two orthogonal concepts are modelled separately and never collapsed into one
boolean:

- *Acceptance*: :class:`CompletionPolicy` decides whether a step is
  ``completed``, ``partial`` or ``failed`` from observed work-item statuses.
- *Scheduler behavior*: :class:`~confflow.domain.resources.OnFailure` decides
  whether remaining items keep running or not.

Whether a downstream step may consume a partial producer is an explicit
:class:`PartialOutputPolicy`, never runtime guesswork.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from .errors import InvalidCompletionPolicyError

__all__ = [
    "CompletionMode",
    "CompletionPolicy",
    "PartialOutputPolicy",
    "StepStatus",
    "WorkItemStatus",
    "evaluate_step_status",
]


class CompletionMode(str, Enum):
    """Acceptance mode for a step's work items.

    Attributes
    ----------
    REQUIRE_ALL
        Every item must complete; any failure, cancellation, or skip fails
        the step.
    ALLOW_PARTIAL
        A step may be ``partial``; see :class:`CompletionPolicy`.
    """

    REQUIRE_ALL = "require_all"
    ALLOW_PARTIAL = "allow_partial"


class PartialOutputPolicy(str, Enum):
    """Whether downstream steps may consume a partial producer.

    Attributes
    ----------
    DENY
        Consuming a partial producer is a compile error.
    ALLOW
        Downstream may be materialized from the accepted completed subset.
    """

    DENY = "deny"
    ALLOW = "allow"


class StepStatus(str, Enum):
    """Terminal status of a step."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkItemStatus(str, Enum):
    """Terminal status of a work item."""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class CompletionPolicy:
    """Acceptance policy for a step's work items.

    Parameters
    ----------
    mode : CompletionMode
        ``require_all`` or ``allow_partial``.
    minimum_success : int | None
        For ``allow_partial``: the minimum number of completed items that
        still accepts the step as ``partial``; ``None`` means at least one.
    partial_output : PartialOutputPolicy
        Whether downstream steps may bind a producer that can be partial.

    Raises
    ------
    InvalidCompletionPolicyError
        Raised when ``minimum_success`` conflicts with the mode.
    """

    mode: CompletionMode = CompletionMode.REQUIRE_ALL
    minimum_success: int | None = None
    partial_output: PartialOutputPolicy = PartialOutputPolicy.DENY

    def __post_init__(self) -> None:
        if not isinstance(self.mode, CompletionMode):
            raise InvalidCompletionPolicyError("mode must be a CompletionMode")
        if not isinstance(self.partial_output, PartialOutputPolicy):
            raise InvalidCompletionPolicyError("partial_output must be a PartialOutputPolicy")
        if self.minimum_success is not None:
            if isinstance(self.minimum_success, bool) or not isinstance(self.minimum_success, int):
                raise InvalidCompletionPolicyError("minimum_success must be an integer or None")
            if self.minimum_success < 1:
                raise InvalidCompletionPolicyError("minimum_success must be >= 1")
            if self.mode is not CompletionMode.ALLOW_PARTIAL:
                raise InvalidCompletionPolicyError(
                    "minimum_success is only meaningful with mode allow_partial"
                )

    @property
    def accepts_partial(self) -> bool:
        """Return whether this policy can accept a partial outcome."""
        return self.mode is CompletionMode.ALLOW_PARTIAL

    def effective_minimum_success(self) -> int:
        """Return the minimum accepted completed count for a partial outcome."""
        if self.minimum_success is not None:
            return self.minimum_success
        return 1

    def to_dict(self) -> dict[str, object]:
        """Return a canonical, JSON-compatible representation."""
        return {
            "mode": self.mode.value,
            "minimum_success": self.minimum_success,
            "partial_output": self.partial_output.value,
        }


def evaluate_step_status(
    policy: CompletionPolicy, item_statuses: Iterable[WorkItemStatus]
) -> StepStatus:
    """Evaluate a step status from observed work-item statuses.

    Rules
    -----
    - No items: ``completed`` (vacuous completion).
    - Any cancellation: ``cancelled`` (an operator action outranks failures).
    - No failures or cancellations: ``completed``.
    - ``require_all`` with failures: ``failed``.
    - ``allow_partial``: ``partial`` when the completed count reaches
      ``minimum_success``, otherwise ``failed``.

    Parameters
    ----------
    policy : CompletionPolicy
        Acceptance policy of the step.
    item_statuses : Iterable[WorkItemStatus]
        Observed terminal statuses.

    Returns
    -------
    StepStatus
        The evaluated step status.
    """
    statuses = tuple(item_statuses)
    if not statuses:
        return StepStatus.COMPLETED
    completed = sum(1 for status in statuses if status is WorkItemStatus.COMPLETED)
    failed = sum(1 for status in statuses if status is WorkItemStatus.FAILED)
    cancelled = sum(1 for status in statuses if status is WorkItemStatus.CANCELLED)
    if cancelled:
        return StepStatus.CANCELLED
    if not failed:
        return StepStatus.COMPLETED
    if policy.mode is CompletionMode.REQUIRE_ALL:
        return StepStatus.FAILED
    if completed >= policy.effective_minimum_success():
        return StepStatus.PARTIAL
    return StepStatus.FAILED
