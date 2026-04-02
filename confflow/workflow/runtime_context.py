#!/usr/bin/env python3

"""Workflow runtime context initialization and management."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Any

from .stats import CheckpointManager, FailureTracker, WorkflowStatsTracker

__all__ = [
    "WorkflowRuntimeContext",
    "initialize_runtime_context",
]


@dataclass
class WorkflowRuntimeContext:
    root_dir: str
    failed_dir: str
    checkpoint: CheckpointManager
    stats_tracker: WorkflowStatsTracker
    failure_tracker: FailureTracker
    resume_from_step: int
    current_input: str | list[str]


def initialize_runtime_context(
    *,
    work_dir: str,
    config_file: str,
    input_files: list[str],
    original_inputs: list[str],
    resume: bool,
    logger: Any,
) -> WorkflowRuntimeContext:
    """Create the runtime directories, trackers, and current input state."""
    root_dir = os.path.abspath(work_dir)
    os.makedirs(root_dir, exist_ok=True)

    failed_dir = os.path.join(root_dir, "failed")
    os.makedirs(failed_dir, exist_ok=True)

    try:
        shutil.copy2(config_file, os.path.join(failed_dir, os.path.basename(config_file)))
    except OSError as e:
        if hasattr(logger, "debug"):
            logger.debug("Failed to copy the config file into the failed directory: %s", e)
        elif hasattr(logger, "warning"):
            logger.warning(f"Failed to copy the config file into the failed directory: {e}")

    if hasattr(logger, "add_file_handler"):
        logger.add_file_handler(os.path.join(root_dir, "confflow.log"))

    checkpoint = CheckpointManager(root_dir)
    stats_tracker = WorkflowStatsTracker(input_files, original_inputs)
    failure_tracker = FailureTracker(failed_dir)

    if not resume:
        failure_tracker.clear_previous()

    resume_from_step = checkpoint.load() if resume else -1
    current_input: str | list[str] = input_files[0] if len(input_files) == 1 else input_files

    return WorkflowRuntimeContext(
        root_dir=root_dir,
        failed_dir=failed_dir,
        checkpoint=checkpoint,
        stats_tracker=stats_tracker,
        failure_tracker=failure_tracker,
        resume_from_step=resume_from_step,
        current_input=current_input,
    )
