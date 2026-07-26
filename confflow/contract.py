#!/usr/bin/env python3

"""Public JobDesk <-> ConfFlow handshake contract.

This module is the **single owner** of the producer-side artifacts of the
cross-repository contract between ConfFlow and JobDesk. JobDesk consumes the
artifact names exclusively through the CLI ``--capabilities --json`` probe
and never imports this module directly.

Stable contract surface
-----------------------
The names exported from ``__all__`` are the public contract. Renaming or
removing any of them is a wire-protocol break and must be coordinated with
the JobDesk consumer.

- ``CAPABILITY_SCHEMA_VERSION`` is the integer ``schema_version`` emitted by
  ``confflow --capabilities --json``. JobDesk rejects payloads whose
  ``schema_version`` does not equal this constant.
- ``RUN_SUMMARY_FILE`` / ``WORKFLOW_STATS_FILE`` / ``WORKFLOW_STATE_FILE``
  are the exact filenames ConfFlow writes into the working directory for a
  workflow run. JobDesk discovers results by reading these filenames.
"""

from __future__ import annotations

__all__ = [
    "CAPABILITY_SCHEMA_VERSION",
    "RUN_SUMMARY_FILE",
    "WORKFLOW_STATS_FILE",
    "WORKFLOW_STATE_FILE",
]

CAPABILITY_SCHEMA_VERSION: int = 2
RUN_SUMMARY_FILE: str = "run_summary.json"
WORKFLOW_STATS_FILE: str = "workflow_stats.json"
WORKFLOW_STATE_FILE: str = ".workflow_state.json"
