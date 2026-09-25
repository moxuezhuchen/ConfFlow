#!/usr/bin/env python3

"""Artifact retention model for V4.

Retention is a persistence concern, never a hidden side effect of a scientific
calculation.  GC eligibility is derived from retention class plus references:
any artifact referenced by a published :class:`~confflow.domain.step_result.StepResult`
or required for resume is never collected, regardless of its declared class.
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "RetentionClass",
    "may_garbage_collect",
]


class RetentionClass(str, Enum):
    """Declared retention intent of an artifact.

    Attributes
    ----------
    RETAINED
        Permanently retained; never garbage collected by ConfFlow.
    INTERMEDIATE
        Collectable once the run is complete and every consumer is done.
    TEMPORARY
        Collectable as soon as every direct consumer is done.
    """

    RETAINED = "retained"
    INTERMEDIATE = "intermediate"
    TEMPORARY = "temporary"


def may_garbage_collect(
    retention: RetentionClass,
    *,
    referenced: bool,
    consumed: bool,
    run_complete: bool,
) -> bool:
    """Return whether an artifact may be garbage collected.

    Parameters
    ----------
    retention : RetentionClass
        Declared retention class of the artifact.
    referenced : bool
        Whether a published step result or resume requirement references it.
    consumed : bool
        Whether every direct consumer of the artifact has finished.
    run_complete : bool
        Whether the run has reached a terminal state.

    Returns
    -------
    bool
        ``True`` only when collection cannot invalidate published results or
        resume state.
    """
    if referenced:
        return False
    if retention is RetentionClass.RETAINED:
        return False
    if retention is RetentionClass.TEMPORARY:
        return consumed
    return consumed and run_complete
