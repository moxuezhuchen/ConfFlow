#!/usr/bin/env python3

"""Fail-closed retirement stubs for the removed V2/V3 workflow runtime.

The V2/V3 workflow execution runtime (engine, state stores, V3 runtime, step
handlers, binding/execution context, stats, presenter, finalize, dataflow,
resume validation, runtime context, and the legacy DAG) was removed by the
post-closure Architecture Diet.  The historical public names remain
importable so tooling can resolve them, but every use raises
:class:`RetiredRuntimeError` with the migration path instead of executing
retired code.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

__all__ = ["RetiredRuntimeError", "retired"]


class RetiredRuntimeError(RuntimeError):
    """Raised when a retired V2/V3 workflow runtime API is used."""


def retired(qualified_name: str) -> Callable[..., Any]:
    """Return a fail-closed callable for the retired name *qualified_name*."""

    def _raise(*args: Any, **kwargs: Any) -> Any:
        raise RetiredRuntimeError(
            f"{qualified_name} is retired: the V2/V3 workflow execution runtime "
            "was removed by the post-closure Architecture Diet. Use the V4 "
            "runtime (``confflow v4 run`` or "
            "``confflow.application.v4_entry.formal_v4_runner``); V2/V3 "
            "documents fail closed with ``legacy_workflow_not_executable`` "
            "and require migration."
        )

    _raise.__name__ = qualified_name.rsplit(".", 1)[-1]
    _raise.__doc__ = f"Retired stub for ``{qualified_name}``; always fails closed."
    return _raise


run_workflow = retired("confflow.workflow.run_workflow")
StepRecord = retired("confflow.workflow.StepRecord")
WorkflowState = retired("confflow.workflow.WorkflowState")
WorkflowStateStore = retired("confflow.workflow.WorkflowStateStore")
CheckpointManager = retired("confflow.workflow.CheckpointManager")
WorkflowStatsTracker = retired("confflow.workflow.WorkflowStatsTracker")
TaskStatsCollector = retired("confflow.workflow.TaskStatsCollector")
FailureTracker = retired("confflow.workflow.FailureTracker")
Tracer = retired("confflow.workflow.Tracer")

#: Names that resolve to fail-closed retirement stubs.
RETIRED_NAMES: tuple[str, ...] = (
    "run_workflow",
    "StepRecord",
    "WorkflowState",
    "WorkflowStateStore",
    "CheckpointManager",
    "WorkflowStatsTracker",
    "TaskStatsCollector",
    "FailureTracker",
    "Tracer",
)
