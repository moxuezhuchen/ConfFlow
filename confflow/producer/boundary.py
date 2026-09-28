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

from ..config.contract_schemas import CONFIGURATION_VALIDATION_SCHEMA
from ..domain.canonical import (
    CANONICALIZATION_ID,
    canonical_json_bytes,
    canonical_sha256,
    typed_digest,
)
from ..workflow.v4.document import SCHEMA_ID as WORKFLOW_SCHEMA_ID
from ..workflow.v4.fingerprint import SEMANTICS_VERSION

__all__ = [
    "AUTHORING_PROTOCOL_SCHEMA",
    "BOUNDARY_PROTOCOL_ID",
    "BOUNDARY_PROTOCOL_VERSION",
    "CANONICALIZATION_ID",
    "COMPATIBILITY_REASON_CODES",
    "COMPATIBILITY_REQUIREMENT_KINDS",
    "COMPATIBILITY_STATUSES",
    "PREPARED_RUN_MANIFEST_SCHEMA",
    "RESULT_MANIFEST_SCHEMA",
    "VALIDATION_RECEIPT_SCHEMA",
    "authoring_protocol_schema",
    "boundary_document",
    "boundary_section",
    "canonicalization_document",
    "canonicalize_text",
    "capability_identity",
    "compare_identities",
    "compatibility_matrix",
    "compatibility_vocabulary",
    "diagnostic_envelope",
    "evaluate_compatibility",
    "identity_relationships",
    "jcs_vectors",
    "prepared_run_manifest_schema",
    "semantic_identity",
    "validation_receipt_schema",
    "wire_examples",
]

#: Identity of the public process boundary: one versioned protocol shared by
#: every boundary operation (contract/authoring/prepare/validate/run/status).
BOUNDARY_PROTOCOL_ID = "confflow.boundary.v4"

#: Wire version of the boundary protocol itself.  Bumped only when an existing
#: member changes meaning; additive members keep the version.
BOUNDARY_PROTOCOL_VERSION = "1.0.0"

#: Content schema of the run-result manifest (the published result wire).
RESULT_MANIFEST_SCHEMA = "confflow.run_result_manifest.v1"

#: Content schema of the PreparedRun manifest (the immutable execution request).
PREPARED_RUN_MANIFEST_SCHEMA = "confflow.prepared_run_manifest.v1"

#: Content schema of one validation receipt.
VALIDATION_RECEIPT_SCHEMA = "confflow.validation_receipt.v1"

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

_SEMANTIC_CONTRACT_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("workflow_schema", WORKFLOW_SCHEMA_ID),
    ("semantics", SEMANTICS_VERSION),
    ("canonicalization", CANONICALIZATION_ID),
    ("result_manifest", RESULT_MANIFEST_SCHEMA),
    ("validation_response", CONFIGURATION_VALIDATION_SCHEMA),
    ("prepared_run_manifest", PREPARED_RUN_MANIFEST_SCHEMA),
    ("validation_receipt", VALIDATION_RECEIPT_SCHEMA),
    ("authoring_protocol", AUTHORING_PROTOCOL_SCHEMA),
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


def _typed_digest_of(kind: str, payload: Any) -> str:
    return typed_digest(kind, payload)


def capability_identity(
    *,
    executors: list[dict[str, Any]],
    execution_adapters: list[dict[str, Any]],
    result_profiles: list[dict[str, Any]],
    scientific_checks: list[dict[str, Any]],
    recovery_profiles: list[dict[str, Any]],
    programs: list[dict[str, Any]],
    analysis_capabilities: list[dict[str, Any]],
    transform_kinds: tuple[str, ...],
) -> dict[str, Any]:
    """Return the capability identity of one producer build.

    The digest covers exactly the descriptors that decide whether a scientific
    requirement can still be executed: executor contracts and ports, adapters,
    profiles, checks, recoveries, program adapters and analysis capabilities.
    Display text, recipe prose, and build provenance are excluded by
    construction.
    """
    components: dict[str, Any] = {
        "executors": executors,
        "execution_adapters": execution_adapters,
        "result_profiles": result_profiles,
        "scientific_checks": scientific_checks,
        "recovery_profiles": recovery_profiles,
        "programs": programs,
        "analysis_capabilities": analysis_capabilities,
        "transform_kinds": list(transform_kinds),
    }
    return {
        "algorithm": "sha256",
        "digest": _typed_digest_of("confflow.boundary.capabilities.v1", components),
        "component_names": sorted(components),
    }


def semantic_identity(result_schema_sha256: str | None = None) -> dict[str, Any]:
    """Return the semantic identity of the boundary protocol.

    Semantic identity answers "do we interpret the same wire the same way":
    workflow schema, semantics version, canonicalization, and every published
    content schema.  It never includes build provenance.
    """
    components = [
        {"name": name, "version": version} for name, version in _SEMANTIC_CONTRACT_COMPONENTS
    ]
    if result_schema_sha256 is not None:
        components.append({"name": "result_schema_sha256", "version": result_schema_sha256})
    return {
        "algorithm": "sha256",
        "digest": _typed_digest_of("confflow.boundary.semantics.v1", components),
        "components": sorted(components, key=lambda item: item["name"]),
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


def compatibility_matrix() -> list[dict[str, str]]:
    """Return the frozen change-class -> decision matrix."""
    return [
        {
            "change": "recipe description, label, display metadata, ordering",
            "decision": "compatible",
            "why": "display-only members are excluded from capability and semantic identity",
        },
        {
            "change": "producer build version/commit/dirty",
            "decision": "needs_revalidation",
            "why": "build provenance is frozen on the receipt and re-verified, never auto-rejected",
        },
        {
            "change": "capability contract version or port contract shape",
            "decision": "unsupported",
            "why": "a required capability can no longer be executed as declared",
        },
        {
            "change": "required capability removed from the registry",
            "decision": "unsupported",
            "why": "the requirement names a capability the producer no longer offers",
        },
        {
            "change": "workflow schema / semantics / canonicalization identity",
            "decision": "unsupported",
            "why": "the wire meaning changed; the document must be migrated, not reinterpreted",
        },
        {
            "change": "content identity changed with the same schema and semantics",
            "decision": "needs_revalidation",
            "why": "the document is structurally compatible but must be re-validated and re-prepared",
        },
    ]


def compare_identities(
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
) -> dict[str, bool]:
    """Compare two capability/semantic identity documents.

    ``previous``/``current`` are the ``capability_identity`` or
    ``semantic_identity`` mappings; a missing side counts as changed so a
    consumer can never treat "unknown" as "same".
    """
    changed = True
    if previous is not None and current is not None:
        changed = previous.get("digest") != current.get("digest")
    return {"changed": changed}


def evaluate_compatibility(
    requirements: list[dict[str, Any]],
    offered: dict[str, Any],
    *,
    content_identity_changed: bool = False,
    build_provenance_changed: bool = False,
) -> dict[str, Any]:
    """Evaluate a requirement set against one producer's offered identity.

    Parameters
    ----------
    requirements :
        Requirement records: ``{"kind": capability|contract|semantic,
        "name": str, "contract_version": str}``.  A semantic requirement may
        carry an empty ``contract_version``, meaning "must be offered at all".
    offered :
        ``{"capabilities": {name: contract_version}, "contracts": {...},
        "semantics_version": str}`` built from one contract envelope.
    content_identity_changed :
        Whether capability/semantic identity moved between the identity the
        requirement set was authored against and the offered identity.
    build_provenance_changed :
        Whether producer build provenance moved.

    Returns
    -------
    dict
        ``{"status", "reasons": [...], "evaluated_requirements": [...]}``.
    """
    reasons: list[dict[str, str]] = []
    evaluated: list[dict[str, Any]] = []
    capabilities = offered.get("capabilities") or {}
    contracts = offered.get("contracts") or {}
    semantics_version = str(offered.get("semantics_version") or "")

    for requirement in requirements:
        if not isinstance(requirement, dict):
            reasons.append(
                {
                    "code": "semantic_requirement_unsatisfied",
                    "detail": f"malformed requirement {requirement!r}",
                }
            )
            evaluated.append({"requirement": requirement, "satisfied": False})
            continue
        kind = str(requirement.get("kind") or "")
        name = str(requirement.get("name") or "")
        wanted = str(requirement.get("contract_version") or "")
        satisfied = False
        if kind == "capability":
            found = capabilities.get(name)
            if found is None:
                reasons.append(
                    {
                        "code": "capability_missing",
                        "detail": f"capability {name!r} is not offered",
                    }
                )
            elif wanted and str(found) != wanted:
                reasons.append(
                    {
                        "code": "capability_version_incompatible",
                        "detail": (
                            f"capability {name!r} is offered as {found!r}, "
                            f"requirement declares {wanted!r}"
                        ),
                    }
                )
            else:
                satisfied = True
        elif kind == "contract":
            found = contracts.get(name)
            if found is None:
                reasons.append(
                    {
                        "code": "contract_missing",
                        "detail": f"contract {name!r} is not published",
                    }
                )
            elif wanted and str(found) != wanted:
                reasons.append(
                    {
                        "code": "contract_version_incompatible",
                        "detail": (
                            f"contract {name!r} is published as {found!r}, "
                            f"requirement declares {wanted!r}"
                        ),
                    }
                )
            else:
                satisfied = True
        elif kind == "semantic":
            if name == "semantics_version":
                if wanted and wanted != semantics_version:
                    reasons.append(
                        {
                            "code": "semantic_version_incompatible",
                            "detail": (
                                f"semantics version is {semantics_version!r}, "
                                f"requirement declares {wanted!r}"
                            ),
                        }
                    )
                else:
                    satisfied = True
            else:
                reasons.append(
                    {
                        "code": "semantic_requirement_unsatisfied",
                        "detail": f"semantic requirement {name!r} is not satisfied",
                    }
                )
        else:
            reasons.append(
                {
                    "code": "semantic_requirement_unsatisfied",
                    "detail": f"unknown requirement kind {kind!r}",
                }
            )
        evaluated.append({"requirement": requirement, "satisfied": satisfied})

    if reasons:
        status = "unsupported"
    elif content_identity_changed or build_provenance_changed:
        status = "needs_revalidation"
        if content_identity_changed:
            reasons.append(
                {
                    "code": "content_identity_changed",
                    "detail": "producer content identity moved; re-validation is required",
                }
            )
        if build_provenance_changed:
            reasons.append(
                {
                    "code": "build_provenance_changed",
                    "detail": "producer build provenance moved; the receipt must be re-verified",
                }
            )
    else:
        status = "compatible"
    return {
        "status": status,
        "reasons": reasons,
        "evaluated_requirements": evaluated,
    }


def validation_receipt_schema() -> dict[str, Any]:
    """Return the JSON Schema of one validation receipt."""
    digest_pattern = "^sha256:[0-9a-f]{64}$"
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ConfFlow validation receipt",
        "type": "object",
        "required": [
            "content_schema",
            "submission_id",
            "snapshot_digest",
            "workflow_bytes_sha256",
            "input_manifest_digest",
            "execution_binding_digest",
            "producer_semantics_identity",
            "producer_build_provenance",
            "ok",
            "diagnostics",
        ],
        "additionalProperties": False,
        "properties": {
            "content_schema": {"const": VALIDATION_RECEIPT_SCHEMA},
            "submission_id": {"type": "string", "minLength": 1},
            "snapshot_digest": {"type": "string", "pattern": digest_pattern},
            "workflow_bytes_sha256": {"type": "string", "pattern": digest_pattern},
            "input_manifest_digest": {"type": "string", "pattern": digest_pattern},
            "execution_binding_digest": {"type": "string", "pattern": digest_pattern},
            "producer_semantics_identity": {
                "type": "object",
                "required": ["algorithm", "digest", "components"],
                "properties": {
                    "algorithm": {"const": "sha256"},
                    "digest": {"type": "string", "pattern": digest_pattern},
                    "components": {"type": "array"},
                },
            },
            "producer_build_provenance": {
                "type": "object",
                "required": ["package", "version"],
                "properties": {
                    "package": {"type": "string", "minLength": 1},
                    "version": {"type": "string", "minLength": 1},
                    "commit": {"type": ["string", "null"]},
                    "dirty": {"type": ["boolean", "null"]},
                },
            },
            "ok": {"type": "boolean"},
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
    }


def prepared_run_manifest_schema() -> dict[str, Any]:
    """Return the JSON Schema of the PreparedRun manifest."""
    digest_pattern = "^sha256:[0-9a-f]{64}$"
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ConfFlow prepared run manifest",
        "type": "object",
        "required": [
            "content_schema",
            "submission_id",
            "snapshot_digest",
            "frozen_workflow_sha256",
            "input_manifest",
            "execution_binding_digest",
            "producer_provenance",
            "compatibility_requirements",
            "validation_receipt",
        ],
        "additionalProperties": False,
        "properties": {
            "content_schema": {"const": PREPARED_RUN_MANIFEST_SCHEMA},
            "submission_id": {"type": "string", "minLength": 1},
            "snapshot_digest": {"type": "string", "pattern": digest_pattern},
            "frozen_workflow_sha256": {"type": "string", "pattern": digest_pattern},
            "input_manifest": {
                "type": "object",
                "required": ["entries"],
                "additionalProperties": False,
                "properties": {
                    "entries": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": [
                                "name",
                                "selection_id",
                                "content_sha256",
                                "locator",
                            ],
                            "additionalProperties": False,
                            "properties": {
                                "name": {"type": "string", "minLength": 1},
                                "selection_id": {"type": "string", "minLength": 1},
                                "content_sha256": {
                                    "type": "string",
                                    "pattern": digest_pattern,
                                },
                                "locator": {"type": "string", "minLength": 1},
                                "structure_ids": {
                                    "type": "array",
                                    "items": {"type": "string", "minLength": 1},
                                },
                            },
                        },
                    },
                    "digest": {"type": "string", "pattern": digest_pattern},
                },
            },
            "execution_binding_digest": {"type": "string", "pattern": digest_pattern},
            "producer_provenance": {
                "type": "object",
                "required": ["package", "version"],
                "properties": {
                    "package": {"type": "string", "minLength": 1},
                    "version": {"type": "string", "minLength": 1},
                    "commit": {"type": ["string", "null"]},
                    "dirty": {"type": ["boolean", "null"]},
                },
            },
            "compatibility_requirements": {"type": "array"},
            "validation_receipt": {
                "type": "object",
                "required": ["receipt_schema", "receipt_digest"],
                "additionalProperties": False,
                "properties": {
                    "receipt_schema": {"const": VALIDATION_RECEIPT_SCHEMA},
                    "receipt_digest": {"type": "string", "pattern": digest_pattern},
                },
            },
        },
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
                "capability_identity",
                "result",
                "diagnostics",
            ],
            "additionalProperties": False,
            "properties": {
                "content_schema": {"const": AUTHORING_PROTOCOL_SCHEMA},
                "operation": {"enum": operation_enum},
                "ok": {"type": "boolean"},
                "capability_identity": {
                    "type": "object",
                    "required": ["algorithm", "digest"],
                },
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


def identity_relationships() -> dict[str, Any]:
    """Return the frozen identity relationships of the execution boundary."""
    return {
        "step_identity": {
            "authority": "workflow document",
            "path": "steps[].id",
            "rule": (
                "the V4 step id is the only business step identity: bindings, "
                "diagnostics, resources, selection and provenance address steps by id"
            ),
            "label": "display only; duplicates are legal",
            "index": "never identity",
            "duplicate": "duplication allocates a new id",
        },
        "submission": {
            "submission_id": "client-authored idempotency key of one prepare attempt",
            "snapshot_digest": "digest over the frozen prepared snapshot bytes",
            "rule": (
                "same submission_id + same snapshot_digest is one admission; "
                "same submission_id + different snapshot_digest is rejected; "
                "'run again' allocates a new submission_id"
            ),
        },
        "run": {
            "run_id": "producer-allocated identity of one accepted run",
            "generation_id": "producer-allocated identity of one run/resume generation",
            "rule": "a superseded generation can never masquerade as current truth",
        },
        "validation": {
            "receipt_digest": "digest over the canonical receipt bytes",
            "rule": "validate reads the published snapshot; submit accepts only the same snapshot identity",
        },
        "result": {
            "content_schema": RESULT_MANIFEST_SCHEMA,
            "run_id": "producer-allocated identity of the published run result",
            "step_id": "V4 step id",
            "result_id": "stable ResultRef identity within the manifest",
            "artifact": "role + checksum + portable run-relative locator",
            "rule": (
                "a result is addressed by run_id plus typed ResultRefs and "
                "artifact locators/checksums; paths are never identity"
            ),
        },
        "cleanup": {
            "policy": (
                "unsubmitted snapshots expire by TTL or explicit cleanup; "
                "an abandoned snapshot is never executed"
            )
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


def wire_examples() -> dict[str, Any]:
    """Return frozen wire examples (producer-validated shapes)."""
    capability_identity_example = capability_identity(
        executors=[
            {
                "capability": "calculation",
                "contract_version": "confflow.contract.calculation.v1",
                "input_ports": [],
                "output_ports": [],
            }
        ],
        execution_adapters=[],
        result_profiles=[],
        scientific_checks=[],
        recovery_profiles=[],
        programs=[],
        analysis_capabilities=[],
        transform_kinds=(),
    )
    return {
        "named_binding": {
            "target": {"target_step_id": "sp_1", "target_port": "structure"},
            "source": {"kind": "run_input", "port": "structures"},
            "selector": None,
            "cardinality": "many",
            "pairing": "per_structure",
            "partial_consumption": None,
        },
        "step_output_binding": {
            "target": {"target_step_id": "sp_1", "target_port": "structure"},
            "source": {"kind": "step_output", "step_id": "opt_1", "port": "structures"},
            "selector": None,
        },
        "resources": {
            "global": {
                "resources": {"cores_per_item": 8, "memory_per_item": "16GiB"},
                "scheduler": {"max_parallel_items": 2},
            },
            "step_override_memory_only": {
                "resources": {"memory_per_item": "32GiB"},
            },
            "step_effective": {
                "cores_per_item": 8,
                "memory_per_item": "32GiB",
                "max_parallel_items": 2,
            },
            "clear_override": "delete the key; never write null",
        },
        "prepared_run_manifest": {
            "content_schema": PREPARED_RUN_MANIFEST_SCHEMA,
            "submission_id": "submission-0001",
            "snapshot_digest": "sha256:" + "0" * 64,
            "frozen_workflow_sha256": "sha256:" + "1" * 64,
            "input_manifest": {
                "entries": [
                    {
                        "name": "structures",
                        "selection_id": "selection-0001",
                        "content_sha256": "sha256:" + "2" * 64,
                        "locator": "run-inputs/structures/0001.xyz",
                        "structure_ids": ["entity-0001"],
                    }
                ],
                "digest": "sha256:" + "3" * 64,
            },
            "execution_binding_digest": "sha256:" + "4" * 64,
            "producer_provenance": {
                "package": "confflow",
                "version": "0.0.0",
                "commit": None,
                "dirty": None,
            },
            "compatibility_requirements": [],
            "validation_receipt": {
                "receipt_schema": VALIDATION_RECEIPT_SCHEMA,
                "receipt_digest": "sha256:" + "5" * 64,
            },
        },
        "capability_identity_example": capability_identity_example,
    }


def boundary_section(
    *,
    executors: list[dict[str, Any]],
    execution_adapters: list[dict[str, Any]],
    result_profiles: list[dict[str, Any]],
    scientific_checks: list[dict[str, Any]],
    recovery_profiles: list[dict[str, Any]],
    programs: list[dict[str, Any]],
    analysis_capabilities: list[dict[str, Any]],
    transform_kinds: tuple[str, ...],
    result_schema_sha256: str | None = None,
) -> dict[str, Any]:
    """Return the compact boundary section embedded in the contract envelope."""
    schemas = {
        "authoring_request": authoring_protocol_schema()["request"],
        "authoring_response": authoring_protocol_schema()["response"],
        "prepared_run_manifest": prepared_run_manifest_schema(),
        "validation_receipt": validation_receipt_schema(),
    }
    return {
        "protocol_id": BOUNDARY_PROTOCOL_ID,
        "protocol_version": BOUNDARY_PROTOCOL_VERSION,
        "canonicalization_id": CANONICALIZATION_ID,
        "workflow_schema_id": WORKFLOW_SCHEMA_ID,
        "capability_identity": capability_identity(
            executors=executors,
            execution_adapters=execution_adapters,
            result_profiles=result_profiles,
            scientific_checks=scientific_checks,
            recovery_profiles=recovery_profiles,
            programs=programs,
            analysis_capabilities=analysis_capabilities,
            transform_kinds=transform_kinds,
        ),
        "semantic_identity": semantic_identity(result_schema_sha256),
        "compatibility": compatibility_vocabulary(),
        "schemas": {
            name: {
                "schema_id": schema.get("title") or name,
                "sha256": canonical_sha256(schema),
            }
            for name, schema in schemas.items()
        },
    }


def boundary_document(
    *,
    executors: list[dict[str, Any]],
    execution_adapters: list[dict[str, Any]],
    result_profiles: list[dict[str, Any]],
    scientific_checks: list[dict[str, Any]],
    recovery_profiles: list[dict[str, Any]],
    programs: list[dict[str, Any]],
    analysis_capabilities: list[dict[str, Any]],
    transform_kinds: tuple[str, ...],
    result_schema_sha256: str | None = None,
) -> dict[str, Any]:
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
        "capability_identity": capability_identity(
            executors=executors,
            execution_adapters=execution_adapters,
            result_profiles=result_profiles,
            scientific_checks=scientific_checks,
            recovery_profiles=recovery_profiles,
            programs=programs,
            analysis_capabilities=analysis_capabilities,
            transform_kinds=transform_kinds,
        ),
        "semantic_identity": semantic_identity(result_schema_sha256),
        "compatibility": {
            **compatibility_vocabulary(),
            "matrix": compatibility_matrix(),
        },
        "identity_relationships": identity_relationships(),
        "diagnostic_envelope": diagnostic_envelope(),
        "schemas": {
            "authoring_request": authoring_protocol_schema()["request"],
            "authoring_response": authoring_protocol_schema()["response"],
            "prepared_run_manifest": prepared_run_manifest_schema(),
            "validation_receipt": validation_receipt_schema(),
        },
        "wire_examples": wire_examples(),
    }
