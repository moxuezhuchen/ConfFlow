#!/usr/bin/env python3

"""Workflow configuration builder compatibility facade."""

from __future__ import annotations

from typing import Any

from ..config.loader import load_workflow_config_file
from .step_naming import build_step_dir_name_map, sanitize_step_dir_name
from .task_config import (
    _itask_label as _task_config_itask_label,
)
from .task_config import (
    _normalize_iprog_label as _task_config_normalize_iprog_label,
)
from .task_config import (
    build_task_config,
    create_runtask_config,
)

__all__ = [
    "sanitize_step_dir_name",
    "build_step_dir_name_map",
    "load_workflow_config",
    "build_task_config",
    "create_runtask_config",
]

_itask_label = _task_config_itask_label
_normalize_iprog_label = _task_config_normalize_iprog_label
def load_workflow_config(config_file: str) -> dict[str, Any]:
    """Load a workflow configuration file."""
    return load_workflow_config_file(config_file)
