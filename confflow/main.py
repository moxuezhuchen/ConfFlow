#!/usr/bin/env python3

"""Script entry point module.

Provides the stable ``confflow.main:main`` console_scripts entry point.
CLI argument parsing is in ``confflow.cli``; workflow execution logic
is in ``confflow.workflow.engine``.
"""

from __future__ import annotations

import sys

from .core.contracts import ExitCode


def _cli_main(args_list: list | None = None):
    """Lazy compatibility hook for the full CLI entrypoint."""
    from .cli import main as cli_main

    return cli_main(args_list)


def main(args_list: list | None = None) -> int:
    """Entry point function (returns exit code)."""
    effective_args = args_list if args_list is not None else sys.argv[1:]
    if effective_args and effective_args[0] == "config":
        from .config_contract import main as config_main

        return config_main(effective_args[1:])
    result = _cli_main(args_list)
    return result if isinstance(result, int) else ExitCode.RUNTIME_ERROR


__all__ = [
    "main",
]
