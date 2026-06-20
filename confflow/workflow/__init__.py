#!/usr/bin/env python3

"""Workflow execution engine.

This package is responsible for:
- Parsing and normalizing configuration
- Executing workflow steps (currently confgen/calc) and producing final reports
- Checkpoint resume and statistics collection

CLI entry point is in ``confflow.cli``.
"""

from __future__ import annotations

from .engine import run_workflow
from .helpers import (
    as_list,
    count_conformers_any,
    count_conformers_in_xyz,
    pushd,
)
from .stats import (
    CheckpointManager,
    FailureTracker,
    TaskStatsCollector,
    Tracer,
    WorkflowStatsTracker,
)
from .validation import validate_inputs_compatible

__all__ = [
    "run_workflow",
    "pushd",
    "as_list",
    "count_conformers_any",
    "count_conformers_in_xyz",
    "validate_inputs_compatible",
    "CheckpointManager",
    "WorkflowStatsTracker",
    "TaskStatsCollector",
    "FailureTracker",
    "Tracer",
]
