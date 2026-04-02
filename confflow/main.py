#!/usr/bin/env python3

"""Script entry point module.

Provides the stable ``confflow.main:main`` console_scripts entry point.
CLI argument parsing is in ``confflow.cli``; workflow execution logic
is in ``confflow.workflow.engine``.
"""

from __future__ import annotations

from .cli import main as _cli_main
from .core.contracts import ExitCode


def main(args_list: list | None = None) -> int:
    """Entry point function (returns exit code)."""
    result = _cli_main(args_list)
    return result if isinstance(result, int) else ExitCode.RUNTIME_ERROR


__all__ = [
    "main",
]
