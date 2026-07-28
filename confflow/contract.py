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
    "OUTPUT_MANIFEST_SCHEMA",
    "OUTPUT_MANIFEST_FILE",
    "CAPABILITY_SCHEMA_VERSION",
    "RUN_SUMMARY_SCHEMA",
    "WORKFLOW_STATS_SCHEMA",
    "WORKFLOW_STATE_SCHEMA",
    "RUN_SUMMARY_FILE",
    "WORKFLOW_STATS_FILE",
    "WORKFLOW_STATE_FILE",
    "RUN_REPORT_FILE",
    "RUN_MIN_XYZ_TEMPLATE",
    "REQUIRED_COMMANDS",
]

# Schema v4 preserves every v3 field and adds producer/executable provenance.
CAPABILITY_SCHEMA_VERSION: int = 4
RUN_SUMMARY_SCHEMA: str = "confflow.run_summary.v1"
WORKFLOW_STATS_SCHEMA: str = "confflow.workflow_stats.v1"
WORKFLOW_STATE_SCHEMA: str = "confflow.workflow_state.v1"
RUN_SUMMARY_FILE: str = "run_summary.json"
WORKFLOW_STATS_FILE: str = "workflow_stats.json"
OUTPUT_MANIFEST_SCHEMA: str = "confflow.output_manifest.v1"
OUTPUT_MANIFEST_FILE: str = "output_manifest.json"
WORKFLOW_STATE_FILE: str = ".workflow_state.json"
RUN_REPORT_FILE: str = "{basename}.txt"
RUN_MIN_XYZ_TEMPLATE: str = "{basename}min.xyz"
REQUIRED_COMMANDS: tuple[str, ...] = (
    "bash", "nohup", "setsid", "xargs", "sha256sum", "mktemp", "base64",
)
