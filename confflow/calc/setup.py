#!/usr/bin/env python3

"""Core calc utilities: logging, config parsing (iprog/itask), and compatibility layer."""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from .constants import ITASK_MAP

try:
    from ..core.utils import (
        UTILS_AVAILABLE,
        get_logger,  # Returns ConfFlowLogger (custom logger), runtime-compatible with logging.Logger
        parse_memory,
    )
    from ..core.utils import (
        parse_iprog as utils_parse_iprog,
    )
    from ..core.utils import (
        parse_itask as utils_parse_itask,
    )
except ImportError:
    UTILS_AVAILABLE = False
    parse_memory = None  # type: ignore

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    def get_logger():  # type: ignore
        return logging.getLogger("confflow.calc")

    def utils_parse_itask(config: dict[str, Any]) -> int:  # type: ignore
        val = config.get("itask", 3)
        if isinstance(val, int):
            return val
        if str(val).isdigit():
            return int(val)
        return ITASK_MAP.get(str(val).lower(), 3)

    def utils_parse_iprog(config: dict[str, Any]) -> int:  # type: ignore
        iprog_val = config.get("iprog", 1)
        if isinstance(iprog_val, str):
            prog_map = {"gaussian": 1, "g16": 1, "orca": 2}
            return prog_map.get(iprog_val.lower(), 2)
        return int(iprog_val)


logger = get_logger()

__all__ = [
    "get_itask",
    "parse_iprog",
    "setup_logging",
]


def get_itask(config: dict[str, Any]) -> int:
    """Parse itask and return the numeric task type."""
    return utils_parse_itask(config)


def parse_iprog(config: dict[str, Any]) -> int:
    """Parse iprog and return the program ID (1: Gaussian, 2: ORCA)."""
    return utils_parse_iprog(config)


def setup_logging(work_dir: str):
    """Set up the logging system."""
    log_file = os.path.join(work_dir, "calc.log")
    if UTILS_AVAILABLE:
        unified_logger = get_logger()
        # ConfFlowLogger supports add_file_handler; skip if falling back to logging.Logger
        if hasattr(unified_logger, "add_file_handler"):
            unified_logger.add_file_handler(log_file)
        return unified_logger

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)],
    )
    return logging.getLogger("confflow.calc")
