#!/usr/bin/env python3

"""ConfFlow calc sub-package.

Recommended public entrypoints:
- ``run_calc_workflow_step`` for workflow -> calc step execution
- ``TaskRunner`` for single-task internals
- ``step_contract`` helpers for calc-step artifact compatibility

``ChemTaskManager`` remains available as a compatibility/facade entry for
standalone manager-based flows and existing imports.
"""

from __future__ import annotations

from .api import CalcStepExecutionResult, run_calc_workflow_step
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
    "CalcStepExecutionResult",
    "run_calc_workflow_step",
    "TaskRunner",
    "ChemTaskManager",
    "get_itask",
    "parse_iprog",
    "setup_logging",
    "get_policy",
]
