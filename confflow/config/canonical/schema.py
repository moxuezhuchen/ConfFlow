"""Versioned, deterministic workflow configuration schema.

Two schema families live here:

* ``confflow.workflow.v2`` — the historical open-bag document. Its JSON Schema,
  its digest and the ``workflow_json_schema`` / ``workflow_schema_sha256`` entry
  points are **unchanged**; existing consumers keep reading the exact same bytes.
* ``confflow.workflow.v3`` — the strict, explicit-graph document, generated from
  the param descriptor registry (:mod:`confflow.config.canonical.param_fields`)
  so the schema and the validator can never keep two drifting allow-lists. It has
  two structural profiles, :attr:`SchemaProfile.DOCUMENT` (a runnable document
  where every step has an ``id``) and :attr:`SchemaProfile.FRAGMENT` (a partial
  recipe/template where a step's ``id`` is still unset). The two share one
  ``$defs`` block and differ only in the step ``required`` list.

The V3 schema is a production artefact advertised by configuration
contract ``v3`` (R7); its digest moved exactly once for the R5-chartered
``params.theory`` vocabulary addition (RFC §18) and is pinned since.
"""

from __future__ import annotations

import copy
from enum import Enum
from typing import Any

from .extensions import EXTENSION_NAMESPACE_PATTERN
from .param_fields import param_properties
from .serialization import canonical_sha256

WORKFLOW_SCHEMA_VERSION_V2 = "confflow.workflow.v2"
WORKFLOW_SCHEMA_VERSION_V3 = "confflow.workflow.v3"

#: The default/legacy schema version. Kept equal to V2 so no existing caller,
#: digest or contract suddenly points at V3.
WORKFLOW_SCHEMA_VERSION = WORKFLOW_SCHEMA_VERSION_V2

#: Workflow-local, immutable V3 step identity grammar (RFC §5.3). The allocator
#: generates the narrower ``sNNN`` / ``s_xxxxxxxx`` shapes; the schema accepts the
#: permissive grammar.
WORKFLOW_V3_ID_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"

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


class SchemaProfile(Enum):
    """Which structural V3 profile a document is checked against."""

    DOCUMENT = "document"
    FRAGMENT = "fragment"


def workflow_json_schema() -> dict[str, Any]:
    return copy.deepcopy(_WORKFLOW_SCHEMA)


def workflow_schema_sha256() -> str:
    return canonical_sha256(_WORKFLOW_SCHEMA)


def _v3_step_core() -> dict[str, Any]:
    """Return the properties common to a DOCUMENT and a FRAGMENT step."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {"$ref": "#/$defs/id"},
            "label": {"type": "string"},
            "type": {"enum": ["calc", "confgen"]},
            "enabled": {"type": "boolean"},
            "inputs": {"type": "array", "items": {"$ref": "#/$defs/stepIdRef"}},
            # ``params`` is refined to the step type's strict vocabulary below.
            "params": {"type": "object"},
            "checkpoint": {"$ref": "#/$defs/checkpoint"},
            "extensions": {"$ref": "#/$defs/namespacedMap"},
            "annotations": {"type": "object", "additionalProperties": True},
        },
        "allOf": [
            {
                "if": {"properties": {"type": {"const": "calc"}}, "required": ["type"]},
                "then": {"properties": {"params": {"$ref": "#/$defs/calcParams"}}},
            },
            {
                "if": {"properties": {"type": {"const": "confgen"}}, "required": ["type"]},
                "then": {"properties": {"params": {"$ref": "#/$defs/confgenParams"}}},
            },
        ],
    }


def _v3_defs() -> dict[str, Any]:
    """Return the shared ``$defs`` both V3 profiles are assembled from."""
    return {
        "id": {
            "type": "string",
            "pattern": WORKFLOW_V3_ID_PATTERN,
        },
        "stepIdRef": {
            "type": "string",
            "pattern": WORKFLOW_V3_ID_PATTERN,
        },
        "namespacedMap": {
            "type": "object",
            "propertyNames": {"pattern": EXTENSION_NAMESPACE_PATTERN},
            "additionalProperties": True,
        },
        "checkpoint": {
            "type": "object",
            "required": ["from_step"],
            "additionalProperties": False,
            "properties": {"from_step": {"$ref": "#/$defs/stepIdRef"}},
        },
        "calcParams": {
            "type": "object",
            "additionalProperties": False,
            "properties": param_properties("calc"),
        },
        "confgenParams": {
            "type": "object",
            "additionalProperties": False,
            "properties": param_properties("confgen"),
        },
        "stepCore": _v3_step_core(),
        "documentStep": {
            "allOf": [
                {"$ref": "#/$defs/stepCore"},
                {"required": ["id", "type", "inputs"]},
            ]
        },
        "fragmentStep": {
            "allOf": [
                {"$ref": "#/$defs/stepCore"},
                {"required": ["type", "inputs"]},
            ]
        },
    }


def workflow_json_schema_v3(profile: SchemaProfile = SchemaProfile.DOCUMENT) -> dict[str, Any]:
    """Return the production ``confflow.workflow.v3`` JSON Schema for a profile."""
    if profile is SchemaProfile.DOCUMENT:
        step_ref = "#/$defs/documentStep"
        schema_id = "https://confflow.dev/schemas/workflow/v3.json"
    else:
        step_ref = "#/$defs/fragmentStep"
        schema_id = "https://confflow.dev/schemas/workflow/v3-fragment.json"
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": schema_id,
        "title": f"ConfFlow workflow V3 ({profile.value})",
        "type": "object",
        "required": ["schema", "steps"],
        "additionalProperties": False,
        "properties": {
            "schema": {"const": WORKFLOW_SCHEMA_VERSION_V3},
            "global": {"type": "object", "additionalProperties": True},
            "steps": {"type": "array", "items": {"$ref": step_ref}},
            "extensions": {"$ref": "#/$defs/namespacedMap"},
            "annotations": {"type": "object", "additionalProperties": True},
        },
        "$defs": _v3_defs(),
    }


def workflow_schema_sha256_v3() -> str:
    """Return the canonical SHA-256 of the V3 **DOCUMENT** schema.

    This is the future public ``confflow.workflow.v3`` document-contract digest.
    """
    return canonical_sha256(workflow_json_schema_v3(SchemaProfile.DOCUMENT))


def workflow_fragment_schema_sha256_v3() -> str:
    """Return the canonical SHA-256 of the V3 **FRAGMENT** schema (internal only)."""
    return canonical_sha256(workflow_json_schema_v3(SchemaProfile.FRAGMENT))
