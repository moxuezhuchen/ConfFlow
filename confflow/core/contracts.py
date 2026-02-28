#!/usr/bin/env python3
"""CLI output contracts and exit code definitions."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from enum import IntEnum

from .console import redirect_console
from .utils import get_logger, redirect_logging_streams

__all__ = [
    "ExitCode",
    "output_txt_path_for_input",
    "cli_output_to_txt",
]

logger = logging.getLogger("confflow.core.contracts")



class ExitCode(IntEnum):
    SUCCESS = 0
    USAGE_ERROR = 1
    RUNTIME_ERROR = 2


def output_txt_path_for_input(input_path: str) -> str:
    abs_input = os.path.abspath(input_path)
    output_dir = os.path.dirname(abs_input)
    output_base = os.path.splitext(os.path.basename(abs_input))[0]
    return os.path.join(output_dir, f"{output_base}.txt")


@contextmanager
def cli_output_to_txt(input_path: str) -> Iterator[str]:
    """Redirect all stdout/stderr to a plain-text .txt file.

    The terminal receives no output.
    The .txt file path is ``<input_stem>.txt`` next to the input file.
    """
    output_path = output_txt_path_for_input(input_path)

    with open(output_path, "w", encoding="utf-8") as out_f:
        with redirect_stdout(out_f), redirect_stderr(out_f):
            redirect_console(sys.stdout)
            try:
                get_logger().redirect_console_handler(sys.stdout)
            except (AttributeError, OSError) as e:
                logger.debug(f"redirect console handler failed on enter: {e}")
            redirect_logging_streams(sys.stdout, include_root=False)
            try:
                yield output_path
            finally:
                try:
                    redirect_console(sys.stdout)
                except (AttributeError, OSError) as e:
                    logger.debug(f"redirect console failed on exit: {e}")
                try:
                    get_logger().redirect_console_handler(sys.stdout)
                except (AttributeError, OSError) as e:
                    logger.debug(f"redirect console handler failed on exit: {e}")
                try:
                    redirect_logging_streams(sys.stdout, include_root=False)
                except (AttributeError, OSError) as e:
                    logger.debug(f"redirect logging streams failed on exit: {e}")
