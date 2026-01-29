#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""脚本入口模块。

该模块仅用于提供稳定的 `confflow.main:main` console_scripts 入口。
CLI 参数解析在 `confflow.cli`，工作流执行逻辑在 `confflow.workflow.engine`。
"""

from __future__ import annotations

from typing import Optional

from .cli import main as _cli_main
from .workflow.engine import create_runtask_config


def main(args_list: Optional[list] = None) -> int:
    """入口函数（返回退出码）。"""
    return _cli_main(args_list)


__all__ = [
    "main",
    "create_runtask_config",
]
