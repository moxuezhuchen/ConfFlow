"""Versioned, deterministic workflow configuration schema.

One schema family lives here:

* ``confflow.workflow.v2`` — the historical open-bag document. Its JSON Schema,
  its digest and the ``workflow_json_schema`` / ``workflow_schema_sha256`` entry
  points are **unchanged**; existing consumers keep reading the exact same bytes.

The never-released ``confflow.workflow.v3`` schema family was retired by the
Architecture Diet (PR-7): V3 was never a published workflow wire format, so its
schema profiles and digests are gone rather than frozen.
"""

from __future__ import annotations

import copy
from typing import Any

from .serialization import canonical_sha256

WORKFLOW_SCHEMA_VERSION_V2 = "confflow.workflow.v2"

#: The default/legacy schema version. Kept equal to V2 so no existing caller,
#: digest or contract suddenly points at V3.
WORKFLOW_SCHEMA_VERSION = WORKFLOW_SCHEMA_VERSION_V2

_WORKFLOW_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://confflow.dev/schemas/workflow/v2.json",
    "title": "ConfFlow workflow",
    "type": "object",
    "properties": {
        "global": {"type": "object", "additionalProperties": True},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": ["confgen", "gen", "calc", "task"]},
                    "enabled": {"type": ["boolean", "integer", "string"]},
                    "params": {"type": "object", "additionalProperties": True},
                    "inputs": {"oneOf": [{"type": "string"}, {"type": "array"}]},
                },
                "additionalProperties": True,
            },
        },
    },
    "additionalProperties": True,
}


def workflow_json_schema() -> dict[str, Any]:
    return copy.deepcopy(_WORKFLOW_SCHEMA)


def workflow_schema_sha256() -> str:
    return canonical_sha256(_WORKFLOW_SCHEMA)
