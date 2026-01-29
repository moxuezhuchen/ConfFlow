#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""ConfFlow calc 子包。

当前模块只暴露正式 API：`TaskRunner`, `ChemTaskManager`, `ResultsDB`, `ResourceMonitor` 以及基础解析/执行支持。
过时的 `run_single_task`、`generate_input_file` 以及 `_` 前缀的助手已在 Phase 3 移除。
"""

from __future__ import annotations

from .db.database import ResultsDB
from .resources import ResourceMonitor
from .components.parser import parse_output
from .components.executor import handle_backups
from .components.task_runner import TaskRunner
from .manager import ChemTaskManager
from .core import get_itask, parse_iprog, setup_logging

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
]
