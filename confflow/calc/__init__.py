#!/usr/bin/env python3

"""ConfFlow calc sub-package.

Exposes the public API: `TaskRunner`, `ChemTaskManager`, `ResultsDB`,
`ResourceMonitor`, and basic parsing/execution support.
Deprecated helpers (`run_single_task`, `generate_input_file`, and
``_``-prefixed utilities) were removed in Phase 3.
"""

from __future__ import annotations

from .components.executor import handle_backups
from .components.parser import parse_output
from .components.task_runner import TaskRunner
from .db.database import ResultsDB
from .manager import ChemTaskManager
from .policies import get_policy
from .resources import ResourceMonitor
from .setup import get_itask, parse_iprog, setup_logging

__all__ = [
    "ResultsDB",
    "ResourceMonitor",
    "parse_output",
    "handle_backups",
    "TaskRunner",
    "ChemTaskManager",
    "get_itask",
    "parse_iprog",
    "setup_logging",
    "get_policy",
]
