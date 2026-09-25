#!/usr/bin/env python3

"""Workflow execution engines.

This package hosts two independent engines:

- the frozen V2/V3 runtime (``engine``, ``v3_runtime`` and friends), kept as
  historical reference and for existing users;
- the greenfield V4 core (``confflow.workflow.v4``), which shares only the
  dependency-free ``confflow.domain`` layer and never imports the V2/V3
  runtime.

The exports below are resolved lazily (PEP 562) so that importing
``confflow.workflow.v4`` does not drag in the legacy runtime, while
``from confflow.workflow import run_workflow`` continues to work.
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "run_workflow": (".engine", "run_workflow"),
    "StepRecord": (".state", "StepRecord"),
    "WorkflowState": (".state", "WorkflowState"),
    "WorkflowStateStore": (".state", "WorkflowStateStore"),
    "WorkflowSupervisor": (".supervisor", "WorkflowSupervisor"),
    "pushd": (".helpers", "pushd"),
    "as_list": (".helpers", "as_list"),
    "count_conformers_any": (".helpers", "count_conformers_any"),
    "count_conformers_in_xyz": (".helpers", "count_conformers_in_xyz"),
    "validate_inputs_compatible": (".validation", "validate_inputs_compatible"),
    "CheckpointManager": (".stats", "CheckpointManager"),
    "WorkflowStatsTracker": (".stats", "WorkflowStatsTracker"),
    "TaskStatsCollector": (".stats", "TaskStatsCollector"),
    "FailureTracker": (".stats", "FailureTracker"),
    "Tracer": (".stats", "Tracer"),
}

__all__ = [*sorted(_LAZY_EXPORTS)]


def __getattr__(name: str) -> Any:
    export = _LAZY_EXPORTS.get(name)
    if export is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(export[0], package=__name__)
    value = getattr(module, export[1])
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
