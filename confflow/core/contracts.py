#!/usr/bin/env python3
"""CLI output contracts and exit code definitions."""

from __future__ import annotations

import logging
import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from enum import IntEnum
from typing import TextIO

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


class _AnsiStripWriter:
    """File-like wrapper that strips ANSI/VT100 escape sequences before writing.

    Rich Console caches its colour-system at construction time (``_color_system``),
    so changing ``_force_terminal`` after the fact is not enough to suppress escape
    codes.  Stripping them here is therefore the most reliable solution.
    """

    _ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

    def __init__(self, f: TextIO) -> None:
        self._f = f

    def write(self, s: str) -> int:
        return int(self._f.write(self._ANSI_RE.sub("", s)))

    def flush(self) -> None:
        self._f.flush()

    def isatty(self) -> bool:
        return False

    def __getattr__(self, name: str):
        return getattr(self._f, name)


@contextmanager
def cli_output_to_txt(input_path: str) -> Iterator[str]:
    """Redirect all stdout/stderr to a plain-text .txt file.

    The terminal receives no output.
    The .txt file path is ``<input_stem>.txt`` next to the input file.
    """
    output_path = output_txt_path_for_input(input_path)
    original_stdout = sys.stdout

    with open(output_path, "w", encoding="utf-8") as out_f:
        stripped = _AnsiStripWriter(out_f)
        with redirect_stdout(stripped), redirect_stderr(stripped):  # type: ignore[arg-type]
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
                    redirect_console(original_stdout)
                except (AttributeError, OSError) as e:
                    logger.debug(f"redirect console failed on exit: {e}")
                try:
                    get_logger().redirect_console_handler(original_stdout)
                except (AttributeError, OSError) as e:
                    logger.debug(f"redirect console handler failed on exit: {e}")
                try:
                    redirect_logging_streams(original_stdout, include_root=False)
                except (AttributeError, OSError) as e:
                    logger.debug(f"redirect logging streams failed on exit: {e}")
