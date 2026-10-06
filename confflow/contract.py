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
  ``confflow --capabilities --json``. JobDesk does not strictly validate
  this value (it only requires the probe to exit zero), so the retired
  artifact fields below were removed without a version bump.
- ``RUN_RESULT_FILE`` / ``RUN_GENERATION_FILE`` are the exact filenames
  ConfFlow writes into the working directory for a workflow run
  (``run_result.json`` / ``run_generation.json``, owned by
  ``confflow.producer.run_result`` and ``confflow.persistence.generation``).
  JobDesk discovers results by reading these filenames.

Retired (L2-CF-contract)
------------------------
``RUN_SUMMARY_SCHEMA``, ``WORKFLOW_STATS_SCHEMA(_V2)``,
``WORKFLOW_STATE_SCHEMA(_V2)``, ``OUTPUT_MANIFEST_SCHEMA(_V2)``,
``RUN_SUMMARY_FILE``, ``WORKFLOW_STATS_FILE``, ``OUTPUT_MANIFEST_FILE``,
``WORKFLOW_STATE_FILE``, ``RUN_REPORT_FILE`` and ``RUN_MIN_XYZ_TEMPLATE``
are deleted. Their only production consumer was the ``--capabilities``
announcement itself (verified: no production reader of the corresponding
files remains; the service reads only ``run_result.json`` /
``run_generation.json``). Announcing them advertised pseudo-capabilities.
"""

from __future__ import annotations

__all__ = [
    "CAPABILITY_SCHEMA_VERSION",
    "REQUIRED_COMMANDS",
    "RUN_GENERATION_FILE",
    "RUN_RESULT_FILE",
]

# Schema v4 preserves every v3 field and adds producer/executable provenance.
CAPABILITY_SCHEMA_VERSION: int = 4
RUN_RESULT_FILE: str = "run_result.json"
RUN_GENERATION_FILE: str = "run_generation.json"
REQUIRED_COMMANDS: tuple[str, ...] = (
    "bash",
    "nohup",
    "setsid",
    "xargs",
    "sha256sum",
    "mktemp",
    "base64",
)
