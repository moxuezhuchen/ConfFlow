"""Versioned, deterministic workflow configuration schema."""

from __future__ import annotations

import copy
from typing import Any

from .serialization import canonical_sha256

WORKFLOW_SCHEMA_VERSION = "confflow.workflow.v2"

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
