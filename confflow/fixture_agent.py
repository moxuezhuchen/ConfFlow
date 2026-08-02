"""Explicit opt-in console entry point for the non-compute lifecycle fixture.

This executable shares the frozen control adapter with ``confflow``.  Its only
additional behavior is the deliberate hand-off of a queued launch intent to
``synthetic_agent_entry`` after the formal control ``execute`` operation has
created that intent.  It never accepts workflow, shell, executable, artifact,
or user-payload arguments.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from . import cli
from .application.execution.synthetic_producer import synthetic_agent_entry
from .control import _snapshot_response, _write_response, run_request
from .core.contracts import ExitCode


def _actual_entrypoint() -> str | None:
    """Resolve the executable that invoked this console entry point."""
    candidate = Path(sys.argv[0])
    if not candidate.is_file():
        candidate = Path(sys.executable).with_name("confflow-fixture-agent")
    if not candidate.is_file():
        return None
    return os.path.realpath(os.path.abspath(os.fspath(candidate)))


def _fixture_after_execute(
    state_root: str, run_id: str, _queued_response: dict[str, Any]
) -> dict[str, Any]:
    """Consume the same durable queued intent and return its final snapshot."""
    snapshot = synthetic_agent_entry(state_root, run_id)
    return _snapshot_response("execute", snapshot)


def main(args_list: list[str] | None = None) -> int:
    """Run the explicitly selected fixture control surface."""
    effective_args = list(args_list if args_list is not None else sys.argv[1:])

    if effective_args and effective_args[0] in {"--capabilities", "--version"}:
        return int(cli.main(effective_args, executable_override=_actual_entrypoint()))

    if not effective_args or effective_args[0] != "control":
        print(
            "confflow-fixture-agent accepts only --capabilities or control operations",
            file=sys.stderr,
        )
        return ExitCode.USAGE_ERROR

    exit_code, response = run_request(
        effective_args[1:],
        post_execute=_fixture_after_execute,
    )
    _write_response(response)
    return exit_code


__all__ = ["main"]
