#!/usr/bin/env python3

"""Producer-owned P0 boundary protocol (confflow.boundary.v4).

This module is the single producer authority for the public JobDesk boundary:
protocol identity, canonicalization identity, JCS test vectors, capability and
semantic identity, the compatibility vocabulary and its decision rule, the
PreparedRun manifest / validation receipt / authoring wire schemas, and the
identity relationships between submission, snapshot, run and generation.

Facts here are *generated* -- JCS vectors come from the real canonicalizer,
capability descriptors come from the real execution registry -- so consumers
verify against producer output instead of hand-written expectations.

Nothing in this module reaches a runtime: it is pure shape, identity and
decision vocabulary.  The prepare/run runtime that consumes these schemas is a
separate, later deliverable.
"""

from __future__ import annotations

import json
from typing import Any

from ..domain.canonical import (
    CANONICALIZATION_ID,
    canonical_json_bytes,
    canonical_sha256,
)
from ..workflow.v4.document import SCHEMA_ID as WORKFLOW_SCHEMA_ID

__all__ = [
    "AUTHORING_PROTOCOL_SCHEMA",
    "BOUNDARY_PROTOCOL_ID",
    "BOUNDARY_PROTOCOL_VERSION",
    "CANONICALIZATION_ID",
    "COMPATIBILITY_REASON_CODES",
    "COMPATIBILITY_REQUIREMENT_KINDS",
    "COMPATIBILITY_STATUSES",
    "RESULT_MANIFEST_SCHEMA",
    "authoring_protocol_schema",
    "boundary_document",
    "boundary_section",
    "canonicalization_document",
    "canonicalize_text",
    "compatibility_vocabulary",
    "diagnostic_envelope",
    "jcs_vectors",
]

#: Identity of the public process boundary: one versioned protocol shared by
#: every boundary operation (contract/authoring/prepare/validate/run/status).
BOUNDARY_PROTOCOL_ID = "confflow.boundary.v4"

#: Wire version of the boundary protocol itself.  Bumped only when an existing
#: member changes meaning; additive members keep the version.
BOUNDARY_PROTOCOL_VERSION = "1.0.0"

#: Content schema of the run-result manifest (the published result wire).
RESULT_MANIFEST_SCHEMA = "confflow.run_result_manifest.v1"

#: Content schema of authoring requests and responses.
AUTHORING_PROTOCOL_SCHEMA = "confflow.authoring.v4"

#: Compatibility decisions a consumer may act on.
COMPATIBILITY_STATUSES: tuple[str, ...] = (
    "compatible",
    "needs_revalidation",
    "unsupported",
)

#: Requirement kinds a compatibility evaluation understands.
COMPATIBILITY_REQUIREMENT_KINDS: tuple[str, ...] = (
    "capability",
    "contract",
    "semantic",
)

#: Stable reason codes carried by a compatibility decision.
COMPATIBILITY_REASON_CODES: tuple[str, ...] = (
    "capability_missing",
    "capability_version_incompatible",
    "contract_missing",
    "contract_version_incompatible",
    "semantic_requirement_unsatisfied",
    "semantic_version_incompatible",
    "capability_identity_changed",
    "content_identity_changed",
    "build_provenance_changed",
)

#: Rejection reason codes a canonicalization attempt may report.
CANONICALIZATION_REASON_CODES: tuple[str, ...] = (
    "invalid_json",
    "duplicate_mapping_key",
    "non_finite_number",
    "integer_out_of_domain",
    "non_canonical_value",
)

#: The producer-owned JCS vectors.  Each entry is the exact input JSON text a
#: consumer parses, plus the canonical bytes and digest the real producer
#: emits -- or the producer's structured rejection.  Consumers may not
#: substitute their own expectations.
_JCS_VECTOR_INPUTS: tuple[tuple[str, str], ...] = (
    ("scalar_float_one", "1.0"),
    ("scalar_float_negative_zero", "-0.0"),
    ("scalar_float_small_exponent", "1e-7"),
    ("scalar_float_large_exponent", "1e21"),
    ("scalar_float_thousandths", "0.1"),
    ("scalar_float_shortest_roundtrip", "333333333.33333329"),
    ("scalar_int_max_safe", "9007199254740991"),
    ("scalar_int_beyond_safe", "9007199254740993"),
    ("unicode_utf16_key_order", '{"\\uff3a":1,"\\ud800\\udc00":2,"a":3}'),
    ("unicode_no_normalization", '{"\\u00e9":1,"e\\u0301":2}'),
    ("nested_objects", '{"b":{"d":[1,2,{"z":null,"a":true}]},"a":[]}'),
    ("array_order_preserved", "[3,1,2]"),
    ("string_escapes", '{"s":"a\\"b\\\\c\\n\\t\\u0000\\u001f"}'),
    ("duplicate_keys_rejected", '{"a":1,"a":2}'),
    ("non_finite_nan_rejected", '{"x":NaN}'),
    ("non_finite_infinity_rejected", '{"x":Infinity}'),
    ("nested_non_finite_rejected", '{"x":[1,-Infinity]}'),
)


class _DuplicateKeyError(ValueError):
    """Raised while parsing JSON text that repeats a mapping key."""


class _NonFiniteConstantError(ValueError):
    """Raised while parsing JSON text that carries NaN/Infinity."""


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise _DuplicateKeyError(key)
        seen.add(key)
        result[key] = value
    return result


def _reject_constant(name: str) -> Any:
    raise _NonFiniteConstantError(name)


def _reason_from_exception(exc: Exception) -> str:
    """Map a canonicalization failure onto a stable reason code."""
    if isinstance(exc, _DuplicateKeyError):
        return "duplicate_mapping_key"
    if isinstance(exc, _NonFiniteConstantError):
        return "non_finite_number"
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_json"
    message = str(exc)
    if "non-finite" in message:
        return "non_finite_number"
    if "safe integer domain" in message:
        return "integer_out_of_domain"
    return "non_canonical_value"


def canonicalize_text(text: bytes | str) -> dict[str, Any]:
    """Canonicalize JSON *text* exactly as the boundary protocol requires.

    Parsing rejects duplicate mapping keys and NaN/Infinity constants; the
    canonical bytes are RFC 8785 (JCS) over the normalized value.

    Returns
    -------
    dict
        ``{"ok": True, "canonical_bytes": bytes, "canonical_json": str,
        "canonical_sha256": str}`` or ``{"ok": False, "reason_code": str,
        "detail": str}``.
    """
    if isinstance(text, bytes):
        try:
            decoded = text.decode("utf-8")
        except UnicodeDecodeError as exc:
            return {"ok": False, "reason_code": "invalid_json", "detail": str(exc)}
    else:
        decoded = text
    try:
        value = json.loads(
            decoded,
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
        )
    except Exception as exc:  # noqa: BLE001 - mapped to a stable reason code
        return {"ok": False, "reason_code": _reason_from_exception(exc), "detail": str(exc)}
    try:
        encoded = canonical_json_bytes(value)
    except Exception as exc:  # noqa: BLE001 - mapped to a stable reason code
        return {"ok": False, "reason_code": _reason_from_exception(exc), "detail": str(exc)}
    return {
        "ok": True,
        "canonical_bytes": encoded,
        "canonical_json": encoded.decode("utf-8"),
        "canonical_sha256": canonical_sha256(value),
    }


def jcs_vectors() -> list[dict[str, Any]]:
    """Return the producer-generated JCS vectors.

    Every vector is produced by running :func:`canonicalize_text` on its exact
    input text now; there is no stored expectation to drift.
    """
    vectors: list[dict[str, Any]] = []
    for vector_id, input_json in _JCS_VECTOR_INPUTS:
        outcome = canonicalize_text(input_json)
        vector: dict[str, Any] = {
            "id": vector_id,
            "input_json": input_json,
        }
        if outcome["ok"]:
            vector.update(
                {
                    "expect": "ok",
                    "canonical_json": outcome["canonical_json"],
                    "canonical_bytes_hex": outcome["canonical_bytes"].hex(),
                    "canonical_sha256": outcome["canonical_sha256"],
                }
            )
        else:
            vector.update(
                {
                    "expect": "rejected",
                    "reason_code": outcome["reason_code"],
                    "detail": outcome["detail"],
                }
            )
        vectors.append(vector)
    return vectors


def canonicalization_document() -> dict[str, Any]:
    """Return the canonicalization contract plus producer-owned vectors."""
    return {
        "canonicalization_id": CANONICALIZATION_ID,
        "algorithm": "RFC 8785 JSON Canonicalization Scheme (JCS)",
        "encoding": "UTF-8",
        "digest": "SHA-256",
        "rules": {
            "mapping_keys": "sorted by UTF-16 code units; never normalized",
            "arrays": "order preserved",
            "numbers": "ECMAScript Number::toString; -0.0 canonicalizes to 0",
            "non_finite": "rejected (non_finite_number)",
            "integers": "rejected beyond the IEEE-754 safe integer domain",
            "duplicates": "rejected (duplicate_mapping_key)",
        },
        "reason_codes": list(CANONICALIZATION_REASON_CODES),
        "vectors": jcs_vectors(),
    }


def compatibility_vocabulary() -> dict[str, Any]:
    """Return the compatibility decision vocabulary and evaluation rule."""
    return {
        "statuses": list(COMPATIBILITY_STATUSES),
        "requirement_kinds": list(COMPATIBILITY_REQUIREMENT_KINDS),
        "reason_codes": list(COMPATIBILITY_REASON_CODES),
        "rule": (
            "Evaluate requirements in order; any missing or version-incompatible "
            "requirement is unsupported. Otherwise a changed content identity or "
            "build provenance is needs_revalidation. Otherwise compatible."
        ),
    }


def authoring_protocol_schema() -> dict[str, Any]:
    """Return the JSON Schema pair (request/response) of the authoring seam."""
    digest_pattern = "^sha256:[0-9a-f]{64}$"
    operation_enum = [
        "describe_step",
        "binding_candidates",
        "instantiate_card",
        "validate_document",
        "check_compatibility",
        "compile_intent",
        "preview_paths",
        "structure_preview",
    ]
    return {
        "request": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "ConfFlow authoring request",
            "type": "object",
            "required": ["content_schema", "operation"],
            "additionalProperties": False,
            "properties": {
                "content_schema": {"const": AUTHORING_PROTOCOL_SCHEMA},
                "operation": {"enum": operation_enum},
                "document": {"type": "object"},
                "document_digest": {"type": "string", "pattern": digest_pattern},
                "parameters": {
                    "type": "object",
                    "properties": {
                        "step_id": {"type": "string", "minLength": 1},
                        "target_step_id": {"type": "string", "minLength": 1},
                        "target_port": {"type": "string", "minLength": 1},
                        "requested_step_id": {"type": "string", "minLength": 1},
                        "binding_choices": {"type": "object"},
                        "requirements": {"type": "array"},
                    },
                },
            },
        },
        "response": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "ConfFlow authoring response",
            "type": "object",
            "required": [
                "content_schema",
                "operation",
                "ok",
                "result",
                "diagnostics",
            ],
            "additionalProperties": False,
            "properties": {
                "content_schema": {"const": AUTHORING_PROTOCOL_SCHEMA},
                "operation": {"enum": operation_enum},
                "ok": {"type": "boolean"},
                "request_document_digest": {
                    "type": ["string", "null"],
                    "pattern": f"({digest_pattern})?",
                },
                "result": {"type": ["object", "array", "null"]},
                "diagnostics": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["code", "severity", "message"],
                        "properties": {
                            "code": {"type": "string", "minLength": 1},
                            "severity": {"type": "string", "minLength": 1},
                            "step_id": {"type": ["string", "null"]},
                            "field_path": {"type": ["string", "null"]},
                            "message": {"type": "string", "minLength": 1},
                        },
                    },
                },
            },
        },
    }


def diagnostic_envelope() -> dict[str, Any]:
    """Return the one diagnostic envelope every boundary answer shares."""
    return {
        "fields": {
            "code": "stable machine code",
            "severity": "error | warning | info",
            "message": "human text",
            "step_id": "V4 step id or null",
            "field_path": "document field path or null",
            "reason": "stable reason code or null (validation answers)",
        },
        "severities": ["error", "warning", "info"],
        "rule": (
            "consumers branch on code/reason/step_id, never on message text; "
            "step_id is always a V4 step id, never an index or a jd_* id"
        ),
    }


def boundary_section() -> dict[str, Any]:
    """Return the compact boundary section embedded in the contract envelope."""
    schemas = {
        "authoring_request": authoring_protocol_schema()["request"],
        "authoring_response": authoring_protocol_schema()["response"],
    }
    return {
        "protocol_id": BOUNDARY_PROTOCOL_ID,
        "protocol_version": BOUNDARY_PROTOCOL_VERSION,
        "canonicalization_id": CANONICALIZATION_ID,
        "workflow_schema_id": WORKFLOW_SCHEMA_ID,
        "compatibility": compatibility_vocabulary(),
        "schemas": {
            name: {
                "schema_id": schema.get("title") or name,
                "sha256": canonical_sha256(schema),
            }
            for name, schema in schemas.items()
        },
    }


def boundary_document() -> dict[str, Any]:
    """Return the full producer boundary document.

    The full document carries the canonicalization vectors and every published
    JSON Schema; the contract envelope embeds only the compact
    :func:`boundary_section`.
    """
    return {
        "content_schema": "confflow.boundary.v4",
        "protocol_id": BOUNDARY_PROTOCOL_ID,
        "protocol_version": BOUNDARY_PROTOCOL_VERSION,
        "canonicalization": canonicalization_document(),
        "compatibility": compatibility_vocabulary(),
        "diagnostic_envelope": diagnostic_envelope(),
        "schemas": {
            "authoring_request": authoring_protocol_schema()["request"],
            "authoring_response": authoring_protocol_schema()["response"],
        },
    }
