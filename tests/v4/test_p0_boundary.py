#!/usr/bin/env python3

"""P0 boundary protocol tests (PR-0 freeze).

These tests lock the producer-owned boundary facts: the protocol identities,
the RFC 8785 vectors consumers verify against, the compatibility decision
vocabulary and rule, the published PreparedRun/receipt/authoring schemas, and
the freshness of the checked-in fixtures.  Consumers (JobDesk) verify the same
fixtures -- this file proves the fixtures are what the real producer emits.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

import confflow
from confflow.domain.canonical import CANONICALIZATION_ID
from confflow.producer.boundary import (
    AUTHORING_PROTOCOL_SCHEMA,
    BOUNDARY_PROTOCOL_ID,
    BOUNDARY_PROTOCOL_VERSION,
    COMPATIBILITY_REASON_CODES,
    COMPATIBILITY_STATUSES,
    PREPARED_RUN_MANIFEST_SCHEMA,
    RESULT_MANIFEST_SCHEMA,
    VALIDATION_RECEIPT_SCHEMA,
    authoring_protocol_schema,
    boundary_document,
    boundary_section,
    capability_identity,
    compare_identities,
    compatibility_matrix,
    evaluate_compatibility,
    jcs_vectors,
    prepared_run_manifest_schema,
    semantic_identity,
    validation_receipt_schema,
    wire_examples,
)
from confflow.producer.contract import (
    build_boundary_document,
    build_configuration_contract_v4,
)
from confflow.workflow.v4.document import SCHEMA_ID as WORKFLOW_SCHEMA_ID
from confflow.workflow.v4.fingerprint import SEMANTICS_VERSION

FIXTURE_DIR = (
    Path(__file__).resolve().parent.parent.parent / "docs" / "internal" / "fixtures" / "p0_boundary"
)

PRODUCER_VERSION = confflow.__version__

#: Producer-locked canonical bytes for the load-bearing JCS vectors.  These
#: are RFC 8785 facts (ES6 number serialization, UTF-16 key order); consumers
#: must reproduce them byte for byte.
_EXACT_JCS: tuple[tuple[str, str], ...] = (
    ("scalar_float_one", "1"),
    ("scalar_float_negative_zero", "0"),
    ("scalar_float_small_exponent", "1e-7"),
    ("scalar_float_large_exponent", "1e+21"),
    ("scalar_float_shortest_roundtrip", "333333333.3333333"),
    ("scalar_int_max_safe", "9007199254740991"),
    ("unicode_utf16_key_order", '{"a":3,"\U00010000":2,"\uff3a":1}'),
    ("unicode_no_normalization", '{"e\u0301":2,"\u00e9":1}'),
    ("array_order_preserved", "[3,1,2]"),
)


def _vector(vector_id: str) -> dict[str, Any]:
    for vector in jcs_vectors():
        if vector["id"] == vector_id:
            return vector
    raise KeyError(vector_id)


class TestCanonicalizationVectors:
    @pytest.mark.parametrize(("vector_id", "expected"), _EXACT_JCS)
    def test_exact_canonical_bytes(self, vector_id: str, expected: str) -> None:
        vector = _vector(vector_id)
        assert vector["expect"] == "ok"
        assert vector["canonical_json"] == expected
        assert bytes.fromhex(vector["canonical_bytes_hex"]).decode("utf-8") == expected

    def test_digest_matches_bytes(self) -> None:
        import hashlib

        vector = _vector("nested_objects")
        expected = hashlib.sha256(bytes.fromhex(vector["canonical_bytes_hex"])).hexdigest()
        assert vector["canonical_sha256"] == expected

    @pytest.mark.parametrize(
        ("vector_id", "reason"),
        (
            ("duplicate_keys_rejected", "duplicate_mapping_key"),
            ("non_finite_nan_rejected", "non_finite_number"),
            ("non_finite_infinity_rejected", "non_finite_number"),
            ("nested_non_finite_rejected", "non_finite_number"),
            ("scalar_int_beyond_safe", "integer_out_of_domain"),
        ),
    )
    def test_rejections(self, vector_id: str, reason: str) -> None:
        vector = _vector(vector_id)
        assert vector["expect"] == "rejected"
        assert vector["reason_code"] == reason

    def test_canonicalization_id(self) -> None:
        assert CANONICALIZATION_ID == "confflow.v4.jcs.v1"


class TestBoundaryIdentity:
    def test_protocol_identity(self) -> None:
        section = boundary_section(
            executors=[],
            execution_adapters=[],
            result_profiles=[],
            scientific_checks=[],
            recovery_profiles=[],
            programs=[],
            analysis_capabilities=[],
            transform_kinds=(),
        )
        assert section["protocol_id"] == BOUNDARY_PROTOCOL_ID
        assert section["protocol_version"] == BOUNDARY_PROTOCOL_VERSION
        assert section["canonicalization_id"] == CANONICALIZATION_ID
        assert section["workflow_schema_id"] == WORKFLOW_SCHEMA_ID

    def test_capability_identity_is_display_inert(self) -> None:
        base = {
            "executors": [{"capability": "calculation", "contract_version": "v1"}],
            "execution_adapters": [],
            "result_profiles": [],
            "scientific_checks": [],
            "recovery_profiles": [],
            "programs": [],
            "analysis_capabilities": [],
            "transform_kinds": (),
        }
        first = capability_identity(**base)
        with_description = capability_identity(
            **{
                **base,
                "executors": [
                    {"capability": "calculation", "contract_version": "v1", "description": "prose"}
                ],
            }
        )
        assert (
            first["digest"] != with_description["digest"]
        ), "descriptor identity covers the full wire descriptor by design"
        assert compare_identities(first, first) == {"changed": False}
        assert compare_identities(first, None) == {"changed": True}

    def test_semantic_identity_components(self) -> None:
        identity = semantic_identity()
        names = {component["name"] for component in identity["components"]}
        assert {
            "workflow_schema",
            "semantics",
            "canonicalization",
            "prepared_run_manifest",
            "validation_receipt",
            "authoring_protocol",
        } <= names
        assert identity["digest"].startswith("sha256:")

    def test_contract_embeds_boundary(self) -> None:
        envelope = build_configuration_contract_v4(producer_version="test")
        boundary = envelope["boundary"]
        assert boundary["protocol_id"] == BOUNDARY_PROTOCOL_ID
        assert boundary["protocol_version"] == BOUNDARY_PROTOCOL_VERSION
        assert boundary["canonicalization_id"] == CANONICALIZATION_ID
        assert boundary["capability_identity"]["digest"].startswith("sha256:")
        assert boundary["semantic_identity"]["digest"].startswith("sha256:")
        assert set(boundary["schemas"]) == {
            "authoring_request",
            "authoring_response",
            "prepared_run_manifest",
            "validation_receipt",
        }
        for schema in boundary["schemas"].values():
            assert len(schema["sha256"]) == 64


class TestCompatibility:
    def test_vocabulary(self) -> None:
        assert COMPATIBILITY_STATUSES == ("compatible", "needs_revalidation", "unsupported")
        assert "capability_missing" in COMPATIBILITY_REASON_CODES
        matrix = compatibility_matrix()
        decisions = {row["decision"] for row in matrix}
        assert decisions == {"compatible", "needs_revalidation", "unsupported"}

    def test_display_only_change_is_not_incompatible(self) -> None:
        decision = evaluate_compatibility(
            [{"kind": "capability", "name": "calculation", "contract_version": "v1"}],
            {
                "capabilities": {"calculation": "v1"},
                "contracts": {},
                "semantics_version": SEMANTICS_VERSION,
            },
        )
        assert decision["status"] == "compatible"

    def test_build_change_is_revalidation_not_unsupported(self) -> None:
        decision = evaluate_compatibility(
            [{"kind": "capability", "name": "calculation", "contract_version": "v1"}],
            {
                "capabilities": {"calculation": "v1"},
                "contracts": {},
                "semantics_version": SEMANTICS_VERSION,
            },
            build_provenance_changed=True,
        )
        assert decision["status"] == "needs_revalidation"
        assert [reason["code"] for reason in decision["reasons"]] == ["build_provenance_changed"]

    def test_missing_capability_is_unsupported(self) -> None:
        decision = evaluate_compatibility(
            [{"kind": "capability", "name": "goat", "contract_version": "v1"}],
            {"capabilities": {}, "contracts": {}, "semantics_version": SEMANTICS_VERSION},
        )
        assert decision["status"] == "unsupported"
        assert decision["reasons"][0]["code"] == "capability_missing"

    def test_unsupported_beats_revalidation(self) -> None:
        decision = evaluate_compatibility(
            [{"kind": "capability", "name": "goat", "contract_version": "v1"}],
            {"capabilities": {}, "contracts": {}, "semantics_version": SEMANTICS_VERSION},
            content_identity_changed=True,
            build_provenance_changed=True,
        )
        assert decision["status"] == "unsupported"

    def test_semantic_version_mismatch_is_unsupported(self) -> None:
        decision = evaluate_compatibility(
            [
                {
                    "kind": "semantic",
                    "name": "semantics_version",
                    "contract_version": "confflow.workflow.v4.semantics.v2",
                }
            ],
            {"capabilities": {}, "contracts": {}, "semantics_version": SEMANTICS_VERSION},
        )
        assert decision["status"] == "unsupported"
        assert decision["reasons"][0]["code"] == "semantic_version_incompatible"


class TestPublishedSchemas:
    def test_wire_examples_conform_to_schemas(self) -> None:
        examples = wire_examples()
        Draft202012Validator(prepared_run_manifest_schema()).validate(
            examples["prepared_run_manifest"]
        )
        receipt = {
            "content_schema": VALIDATION_RECEIPT_SCHEMA,
            "submission_id": "submission-0001",
            "snapshot_digest": "sha256:" + "0" * 64,
            "workflow_bytes_sha256": "sha256:" + "1" * 64,
            "input_manifest_digest": "sha256:" + "2" * 64,
            "execution_binding_digest": "sha256:" + "3" * 64,
            "producer_semantics_identity": semantic_identity(),
            "producer_build_provenance": {
                "package": "confflow",
                "version": "0.0.0",
                "commit": None,
                "dirty": None,
            },
            "ok": True,
            "diagnostics": [],
        }
        Draft202012Validator(validation_receipt_schema()).validate(receipt)

    def test_authoring_schemas_expose_operations(self) -> None:
        schemas = authoring_protocol_schema()
        Draft202012Validator.check_schema(schemas["request"])
        Draft202012Validator.check_schema(schemas["response"])
        operations = schemas["request"]["properties"]["operation"]["enum"]
        assert operations == [
            "describe_step",
            "binding_candidates",
            "instantiate_card",
            "validate_document",
            "check_compatibility",
            "compile_intent",
            "preview_paths",
        ]

    def test_schema_ids(self) -> None:
        assert RESULT_MANIFEST_SCHEMA == "confflow.run_result_manifest.v1"
        assert PREPARED_RUN_MANIFEST_SCHEMA == "confflow.prepared_run_manifest.v1"
        assert VALIDATION_RECEIPT_SCHEMA == "confflow.validation_receipt.v1"
        assert AUTHORING_PROTOCOL_SCHEMA == "confflow.authoring.v4"


class TestFixtureFreshness:
    """The checked-in fixtures must equal what the producer generates now."""

    @staticmethod
    @pytest.fixture(scope="class")
    def generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
        import scripts.generate_p0_boundary_fixtures as generator

        out_dir = tmp_path_factory.mktemp("p0_boundary")
        generator.generate(out_dir)
        return out_dir

    @pytest.mark.parametrize(
        "name",
        (
            "boundary_protocol.json",
            "jcs_vectors.json",
            "compatibility_cases.json",
            "named_binding_workflow.json",
        ),
    )
    def test_fixture_is_current(self, generated: Path, name: str) -> None:
        checked_in = FIXTURE_DIR / name
        assert checked_in.is_file(), f"missing producer fixture {name}; run the generator"
        assert (
            checked_in.read_bytes() == (generated / name).read_bytes()
        ), f"{name} is stale; regenerate docs/internal/fixtures/p0_boundary"

    def test_jcs_fixture_carries_producer_vectors(self) -> None:
        payload = json.loads((FIXTURE_DIR / "jcs_vectors.json").read_text(encoding="utf-8"))
        assert payload["canonicalization_id"] == CANONICALIZATION_ID
        assert len(payload["vectors"]) == len(jcs_vectors())
        for vector in payload["vectors"]:
            assert vector["id"]
            assert vector["expect"] in {"ok", "rejected"}

    def test_named_binding_fixture_validates(self) -> None:
        payload = json.loads(
            (FIXTURE_DIR / "named_binding_workflow.json").read_text(encoding="utf-8")
        )
        assert payload["validation"]["ok"] is True
        assert payload["validation"]["step_ids"] == ["opt_1", "sp_1"]

    def test_boundary_document_is_deterministic(self) -> None:
        first = boundary_document(
            executors=[],
            execution_adapters=[],
            result_profiles=[],
            scientific_checks=[],
            recovery_profiles=[],
            programs=[],
            analysis_capabilities=[],
            transform_kinds=(),
        )
        second = build_boundary_document(producer_version=PRODUCER_VERSION)
        assert first["protocol_id"] == second["protocol_id"]
        assert first["canonicalization"]["canonicalization_id"] == (
            second["canonicalization"]["canonicalization_id"]
        )
