#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""calc 核心工具：日志、配置解析（iprog/itask）与兼容层。"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Dict

from .constants import ITASK_MAP

try:
    from ..core.utils import (
        get_logger,  # 返回 ConfFlowLogger（自定义 logger），运行期兼容 logging.Logger 接口
        parse_iprog as utils_parse_iprog,
        parse_itask as utils_parse_itask,
        parse_memory,
        UTILS_AVAILABLE,
    )
except Exception:
    UTILS_AVAILABLE = False
    parse_memory = None  # type: ignore

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    def get_logger():  # type: ignore
        return logging.getLogger("confflow.calc")

    def utils_parse_itask(config: Dict[str, Any]) -> int:  # type: ignore
        val = config.get("itask", 3)
        if isinstance(val, int):
            return val
        if str(val).isdigit():
            return int(val)
        return ITASK_MAP.get(str(val).lower(), 3)

    def utils_parse_iprog(config: Dict[str, Any]) -> int:  # type: ignore
        iprog_val = config.get("iprog", 1)
        if isinstance(iprog_val, str):
            prog_map = {"gaussian": 1, "g16": 1, "orca": 2}
            return prog_map.get(iprog_val.lower(), 2)
        return int(iprog_val)


logger = get_logger()


def get_itask(config: Dict[str, Any]) -> int:
    """解析 itask，返回数值任务类型。"""
    return utils_parse_itask(config)


def parse_iprog(config: Dict[str, Any]) -> int:
    """解析 iprog，返回程序 ID（1: Gaussian, 2: ORCA）。"""
    return utils_parse_iprog(config)


def setup_logging(work_dir: str):
    """设置日志系统。"""
    log_file = os.path.join(work_dir, "calc.log")
    if UTILS_AVAILABLE:
        unified_logger = get_logger()
        # ConfFlowLogger 支持 add_file_handler；若回退到 logging.Logger 则跳过
        if hasattr(unified_logger, "add_file_handler"):
            unified_logger.add_file_handler(log_file)
        return unified_logger

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)],
    )
    return logging.getLogger("confflow.calc")
