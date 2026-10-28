"""Silence logging for machine-readable CLI probes before any heavy import.

Entry modules (``confflow.cli``, ``confflow.control_worker``) import this
module before their dependency-heavy imports. Importing it applies the
probe check to the process argv, so probe output stays free of logging
noise. The root package and the execution layer never import it, so
embedding ConfFlow as a library leaves the host's logging state untouched.
"""

from __future__ import annotations

import logging
import sys


def _is_control_worker_json(args: list[str]) -> bool:
    """Return whether argv is the control worker's JSON invocation."""
    return (
        "--json" in args and "--state-root" in args and "--run-id" in args and "--handoff" in args
    )


def is_handshake_probe(args: list[str]) -> bool:
    """Return whether argv is a machine-readable probe whose output must stay clean."""
    return (
        any(flag in args for flag in ("--version", "--capabilities"))
        or (args[:1] == ["control"] and "--json" in args)
        or (args[:1] == ["config"] and "--json" in args)
        or _is_control_worker_json(args)
    )


#: True when the current process was started as a machine-readable probe.
HANDSHAKE_PROBE: bool = is_handshake_probe(sys.argv[1:])

#: True for the version/capability probes, whose handler restores logging.
VERSION_OR_CAPABILITIES_PROBE: bool = any(
    flag in sys.argv[1:] for flag in ("--version", "--capabilities")
)

if HANDSHAKE_PROBE:
    logging.disable(logging.WARNING)
