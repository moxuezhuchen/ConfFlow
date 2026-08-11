"""Generated workflow-schema metadata shared by parser and release checks."""

from __future__ import annotations

import json
from dataclasses import fields
from typing import Any

from .models import GlobalOptions

SCHEMA_GENERATOR_VERSION = "confflow-schema-generator.v1"
WORKFLOW_SCHEMA_VERSION = "v1"


def schema_document() -> dict[str, Any]:
    """Build the structural schema from canonical model field metadata."""
    global_properties: dict[str, dict[str, object]] = {
        field.name: {} for field in fields(GlobalOptions)
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://confflow.dev/schema/workflow-config/v1",
        "title": "ConfFlow workflow configuration",
        "type": "object",
        "required": ["steps"],
        "properties": {
            "global": {
                "type": "object",
                "properties": global_properties,
                "additionalProperties": True,
            },
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["type"],
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "type": {"type": "string", "enum": ["calc", "confgen", "gen", "task"]},
                        "enabled": {"type": "boolean"},
                        "params": {"type": "object", "additionalProperties": True},
                        "inputs": {"type": "array", "items": {"type": "string", "minLength": 1}},
                    },
                    "additionalProperties": True,
                },
            },
        },
        "additionalProperties": True,
        "x-confflow-generator": SCHEMA_GENERATOR_VERSION,
        "x-confflow-semantic-version": WORKFLOW_SCHEMA_VERSION,
    }


def schema_bytes() -> bytes:
    return (json.dumps(schema_document(), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


__all__ = ["SCHEMA_GENERATOR_VERSION", "WORKFLOW_SCHEMA_VERSION", "schema_bytes", "schema_document"]
