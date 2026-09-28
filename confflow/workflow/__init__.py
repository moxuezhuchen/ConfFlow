#!/usr/bin/env python3

"""Workflow compatibility surface.

The V2/V3 workflow execution runtime was removed by the post-closure
Architecture Diet.  The retired runtime names resolve to fail-closed stubs
(:mod:`confflow.workflow._retired_runtime`), while the diagnostic planners
(``plan``, ``helpers``, ``step_naming``, ``validation``) stay importable for
``--dry-run`` / ``--config-show`` and migration readers.

The exports below are resolved lazily (PEP 562) so that importing
``confflow.workflow.v4`` does not drag in the V2/V3 planner or config layer.
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "run_workflow": ("._retired_runtime", "run_workflow"),
    "StepRecord": ("._retired_runtime", "StepRecord"),
    "WorkflowState": ("._retired_runtime", "WorkflowState"),
    "WorkflowStateStore": ("._retired_runtime", "WorkflowStateStore"),
    "pushd": (".helpers", "pushd"),
    "as_list": (".helpers", "as_list"),
    "count_conformers_any": (".helpers", "count_conformers_any"),
    "count_conformers_in_xyz": (".helpers", "count_conformers_in_xyz"),
    "validate_inputs_compatible": (".validation", "validate_inputs_compatible"),
    "CheckpointManager": ("._retired_runtime", "CheckpointManager"),
    "WorkflowStatsTracker": ("._retired_runtime", "WorkflowStatsTracker"),
    "TaskStatsCollector": ("._retired_runtime", "TaskStatsCollector"),
    "FailureTracker": ("._retired_runtime", "FailureTracker"),
    "Tracer": ("._retired_runtime", "Tracer"),
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
