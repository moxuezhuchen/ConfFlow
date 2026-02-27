#!/usr/bin/env python3

"""Workflow execution engine.

This package is responsible for:
- Parsing and normalizing configuration
- Executing steps (confgen/calc/refine/viz)
- Checkpoint resume and statistics collection

CLI entry point is in ``confflow.cli``.
"""

from __future__ import annotations

from .config_builder import build_task_config, create_runtask_config
from .engine import load_workflow_config, run_workflow
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
    "load_workflow_config",
    "pushd",
    "as_list",
    "count_conformers_any",
    "count_conformers_in_xyz",
    "validate_inputs_compatible",
    "build_task_config",
    "create_runtask_config",
    "CheckpointManager",
    "WorkflowStatsTracker",
    "TaskStatsCollector",
    "FailureTracker",
    "Tracer",
]
