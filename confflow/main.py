#!/usr/bin/env python3

"""Script entry point module.

Provides the stable ``confflow.main:main`` console_scripts entry point.
CLI argument parsing is in ``confflow.cli``; workflow execution logic
is in ``confflow.workflow.engine``.
"""

from __future__ import annotations

from .cli import main as _cli_main


def main(args_list: list | None = None) -> int:
    """Entry point function (returns exit code)."""
    return _cli_main(args_list)  # type: ignore[no-any-return]


__all__ = [
    "main",
]
