#!/usr/bin/env python3

"""Public JobDesk <-> ConfFlow handshake contract.

Single owner of producer-side artifacts; JobDesk consumes via
`--capabilities --json` probe and never imports this module.

`__all__` names are the public contract; renaming/removing is a
wire-protocol break requiring JobDesk coordination.
`CAPABILITY_SCHEMA_VERSION` is the emitted `schema_version` (JobDesk
only requires probe exit zero, so retired fields removed sans bump).
`RUN_RESULT_FILE`/`RUN_GENERATION_FILE` are exact filenames JobDesk
reads for results (owned by producer.run_result/persistence.generation).
Retired schemas/files (RUN_SUMMARY/WORKFLOW_STATS/OUTPUT_MANIFEST/
WORKFLOW_STATE/RUN_REPORT/RUN_MIN_XYZ_TEMPLATE) deleted: sole consumer
was the announcement itself; no production reader remains.
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
