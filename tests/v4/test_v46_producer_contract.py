#!/usr/bin/env python3

"""V4-6 producer contract envelope tests (frozen wire shape + drift gates).

Every expectation is generated from the single sources -- the V4 schema, the
execution/program registries, the domain vocabularies -- never from copied
lists. The drift gates fail closed: a schema change without a manifest update,
a registry change without a contract rebuild, or an uncompilable recipe is a
test failure, not a silent skew.
"""

from __future__ import annotations

import copy
import importlib
import json
from typing import Any

import yaml

from confflow.config.canonical.contract import CONFIGURATION_VALIDATION_SCHEMA
from confflow.domain.canonical import canonical_json_bytes, canonical_sha256
from confflow.execution.contracts import ExecutorCapability
from confflow.execution.native import ProgramName
from confflow.execution.registry import build_default_registry, default_registry
from confflow.producer import (
    ANALYSIS_REACTION_PROFILE_CAPABILITY,
    ANALYSIS_REACTION_PROFILE_CONTRACT,
    CONFIGURATION_CONTRACT_V4_SCHEMA,
    RESULT_MANIFEST_SCHEMA,
    build_configuration_contract_v4,
    build_run_result_manifest,
    contract_digest_of,
    generate_contract_bytes,
    run_result_json_schema,
    run_result_schema_sha256,
    validate_workflow_bytes,
)
from confflow.producer.contract import SCIENTIFIC_OVERRIDE_KEYS
from confflow.producer.manifest import build_editor_manifest_v4
from confflow.producer.recipes import build_recipe_catalog_v4
from confflow.remote.envelope import HANDOFF_SCHEMA_V2, RESULT_SCHEMA_V2
from confflow.workflow.v4.compiler import compile_workflow
from confflow.workflow.v4.document import SCHEMA_ID, ScientificDefinition
from confflow.workflow.v4.schema import TRANSFORM_KINDS, build_workflow_json_schema

PRODUCER_VERSION = "4.6.0-test"

REQUIRED_ENVELOPE_KEYS = (
    "content_schema",
    "producer",
    "contract_digest",
    "workflow_schema",
    "workflow_schema_sha256",
    "editor_manifest",
    "editor_manifest_sha256",
    "recipe_catalog",
    "recipe_catalog_sha256",
    "executors",
    "programs",
    "execution_adapters",
    "result_profiles",
    "scientific_checks",
    "recovery_profiles",
    "ports",
    "resources",
    "policies",
    "native_escape_hatches",
    "analysis_capabilities",
    "result_schema",
    "result_schema_sha256",
    "validation_response_schema",
    "remote_capability",
)

LEGACY_OUTPUT_TOKENS = (
    "result.xyz",
    "failed.xyz",
    "workflow_stats.json",
    ".workflow_state.json",
    "output_path",
    "min_xyz",
)


def _envelope() -> dict[str, Any]:
    return build_configuration_contract_v4(producer_version=PRODUCER_VERSION)


class TestEnvelopeShape:
    def test_schema_id_and_required_keys(self) -> None:
        envelope = _envelope()
        assert envelope["content_schema"] == CONFIGURATION_CONTRACT_V4_SCHEMA
        assert envelope["content_schema"] == "confflow.configuration-contract.v4"
        for key in REQUIRED_ENVELOPE_KEYS:
            assert key in envelope, key

    def test_producer_block_echoes_inputs(self) -> None:
        envelope = build_configuration_contract_v4(
            producer_version="9.9", producer_commit="abc123", producer_dirty=True
        )
        assert envelope["producer"] == {
            "package": "confflow",
            "version": "9.9",
            "commit": "abc123",
            "dirty": True,
        }
        defaulted = _envelope()
        assert defaulted["producer"] == {
            "package": "confflow",
            "version": PRODUCER_VERSION,
            "commit": None,
            "dirty": None,
        }

    def test_validation_schema_stays_v1(self) -> None:
        assert CONFIGURATION_VALIDATION_SCHEMA == "confflow.configuration-validation.v1"
        assert _envelope()["validation_response_schema"] == CONFIGURATION_VALIDATION_SCHEMA

    def test_contract_digest_covers_envelope_minus_digest(self) -> None:
        envelope = _envelope()
        assert envelope["contract_digest"] == contract_digest_of(envelope)
        unsigned = {k: v for k, v in envelope.items() if k != "contract_digest"}
        assert envelope["contract_digest"] == canonical_sha256(unsigned)

    def test_artifact_digests_cover_artifacts_alone(self) -> None:
        envelope = _envelope()
        for name in ("workflow_schema", "editor_manifest", "recipe_catalog", "result_schema"):
            assert envelope[f"{name}_sha256"] == canonical_sha256(envelope[name]), name

    def test_deterministic_bytes(self) -> None:
        first = generate_contract_bytes(producer_version=PRODUCER_VERSION)
        second = generate_contract_bytes(producer_version=PRODUCER_VERSION)
        assert first == second
        assert (
            json.loads(first.decode("utf-8"))["contract_digest"]
            == json.loads(second.decode("utf-8"))["contract_digest"]
        )

    def test_no_legacy_truth_as_authoritative_output(self) -> None:
        envelope = _envelope()
        blob = canonical_json_bytes(envelope).decode("utf-8")
        catalog_blob = canonical_json_bytes(envelope["recipe_catalog"]).decode("utf-8")
        for token in LEGACY_OUTPUT_TOKENS:
            assert token not in blob, token
            assert token not in catalog_blob, token


class TestGeneratedFromRegistries:
    def test_workflow_schema_is_the_real_v4_schema(self) -> None:
        envelope = _envelope()
        live = build_workflow_json_schema()
        assert envelope["workflow_schema"] == live
        assert envelope["workflow_schema_id"] == SCHEMA_ID
        assert SCHEMA_ID == "confflow.workflow.v4"

    def test_executors_match_registry(self) -> None:
        registry = default_registry()
        envelope = _envelope()
        assert [e["capability"] for e in envelope["executors"]] == sorted(registry.capability_names)
        for entry in envelope["executors"]:
            contract = registry.executor(ExecutorCapability(entry["capability"]))
            assert entry["contract_version"] == contract.contract_version
            assert entry["output_ports"], entry["capability"]

    def test_adapters_profiles_checks_recovery_match_registry(self) -> None:
        registry = default_registry()
        envelope = _envelope()
        assert [a["name"] for a in envelope["execution_adapters"]] == sorted(registry.adapter_names)
        assert [p["name"] for p in envelope["result_profiles"]] == sorted(registry.profile_names)
        assert [c["name"] for c in envelope["scientific_checks"]] == sorted(registry.check_names)
        assert [r["name"] for r in envelope["recovery_profiles"]] == sorted(registry.recovery_names)
        for check in envelope["scientific_checks"]:
            assert check["contract_version"] == registry.check(check["name"]).contract_version
        for recovery in envelope["recovery_profiles"]:
            assert (
                recovery["contract_version"] == registry.recovery(recovery["name"]).contract_version
            )

    def test_programs_resolve_through_program_registry(self) -> None:
        envelope = _envelope()
        assert [p["program"] for p in envelope["programs"]] == sorted(
            program.value for program in ProgramName
        )
        for descriptor in envelope["programs"]:
            assert descriptor["aliases"], descriptor["program"]
            assert descriptor["adapter_version"]
            assert descriptor["parser_version"]
            assert descriptor["default_executable"]

    def test_ports_pairing_cardinality_from_domain_and_registry(self) -> None:
        envelope = _envelope()
        ports = envelope["ports"]
        assert ports["vocabulary"]["cardinalities"] == ["one", "optional", "one_or_more", "many"]
        assert ports["vocabulary"]["pairings"] == [
            "single",
            "per_structure",
            "by_subject",
            "by_group_key",
        ]
        assert ports["allowed_pairings_by_kind"]["structure"] == [
            "single",
            "per_structure",
            "by_group_key",
        ]
        owners = [entry["owner"] for entry in ports["port_index"]]
        assert "executor:calculation" in owners
        assert "adapter:standard" in owners

    def test_resource_completion_scheduler_native_sections(self) -> None:
        envelope = _envelope()
        resource_names = [f["name"] for f in envelope["resources"]["fields"]]
        assert resource_names == [
            "cores_per_item",
            "memory_per_item",
            "max_parallel_items",
            "on_failure",
        ]
        assert envelope["policies"]["completion"]["modes"] == ["require_all", "allow_partial"]
        assert envelope["policies"]["scheduler"]["on_failure"] == ["continue", "fail_fast"]
        assert [b["block"] for b in envelope["native_escape_hatches"]["blocks"]] == [
            "calculation.native",
            "confgen.native",
            "transform.native",
            "analysis.native",
        ]
        assert envelope["native_escape_hatches"]["overrides"]["allowlist"] == list(
            SCIENTIFIC_OVERRIDE_KEYS
        )
        assert envelope["transform_kinds"] == list(TRANSFORM_KINDS)

    def test_scientific_override_keys_match_domain_rule(self) -> None:
        assert SCIENTIFIC_OVERRIDE_KEYS == ("charge", "multiplicity", "freeze")
        for key in SCIENTIFIC_OVERRIDE_KEYS:
            ScientificDefinition(overrides={key: 1} if key != "freeze" else {key: [1]})
        try:
            ScientificDefinition(overrides={"keyword": "B3LYP"})
        except Exception:
            pass
        else:  # pragma: no cover - the domain must keep rejecting unknown keys
            raise AssertionError("ScientificDefinition accepted an unknown override")

    def test_remote_capability_ids(self) -> None:
        assert HANDOFF_SCHEMA_V2 == "confflow.control.worker-handoff.v2"
        assert RESULT_SCHEMA_V2 == "confflow.control.worker-result.v2"
        assert _envelope()["remote_capability"] == {
            "handoff": HANDOFF_SCHEMA_V2,
            "result": RESULT_SCHEMA_V2,
        }


class TestAnalysisCapabilities:
    def test_frozen_reaction_profile_advertised(self) -> None:
        assert ANALYSIS_REACTION_PROFILE_CAPABILITY == "reaction_profile"
        assert (
            ANALYSIS_REACTION_PROFILE_CONTRACT == "confflow.contract.analysis.reaction_profile.v1"
        )
        envelope = _envelope()
        assert envelope["analysis_capabilities"]["capabilities"] == [
            {
                "capability": "reaction_profile",
                "contract_version": "confflow.contract.analysis.reaction_profile.v1",
            }
        ]

    def test_registry_appearance_with_different_strings_fails_loudly(self) -> None:
        try:
            registry_module = importlib.import_module("confflow.analysis.registry")
        except ImportError:
            assert _envelope()["analysis_capabilities"]["source"] == "frozen"
            return
        capabilities = registry_module.capabilities()
        by_name = {item["capability"]: item["contract_version"] for item in capabilities}
        assert by_name.get("reaction_profile") == (
            "confflow.contract.analysis.reaction_profile.v1"
        ), "analysis registry appeared with different strings; update the frozen contract"


class TestDriftGates:
    def test_schema_change_without_manifest_fails(self) -> None:
        live = build_workflow_json_schema()
        tampered = copy.deepcopy(live)
        tampered["$defs"].pop("StepModel", None)
        try:
            build_editor_manifest_v4(schema=tampered)
        except ValueError:
            pass
        else:  # pragma: no cover - the gate must stay closed
            raise AssertionError("manifest build accepted a schema without StepModel")

    def test_schema_property_removal_without_manifest_fails(self) -> None:
        live = build_workflow_json_schema()
        tampered = copy.deepcopy(live)
        calculation = tampered["$defs"]["CalculationModel"]
        calculation["properties"].pop("program", None)
        try:
            build_editor_manifest_v4(schema=tampered)
        except ValueError:
            pass
        else:  # pragma: no cover - the gate must stay closed
            raise AssertionError("manifest build accepted a schema without program")

    def test_registry_change_without_contract_fails(self) -> None:
        stale = _envelope()
        extended = build_default_registry()
        from confflow.execution.contracts import CheckSpec

        extended.register_check(
            CheckSpec("drift_probe_check", "confflow.contract.check.drift_probe.v1", "probe")
        )
        rebuilt = build_configuration_contract_v4(
            producer_version=PRODUCER_VERSION, registry=extended
        )
        assert rebuilt["contract_digest"] != stale["contract_digest"]
        assert "drift_probe_check" in [c["name"] for c in rebuilt["scientific_checks"]]
        assert "drift_probe_check" not in [c["name"] for c in stale["scientific_checks"]]

    def test_artifact_digests_are_distinct_per_artifact(self) -> None:
        envelope = _envelope()
        digests = [
            envelope[f"{name}_sha256"]
            for name in ("workflow_schema", "editor_manifest", "recipe_catalog", "result_schema")
        ]
        assert len(set(digests)) == len(digests)


class TestResultManifest:
    def test_result_schema_shape(self) -> None:
        schema = run_result_json_schema()
        assert schema["properties"]["content_schema"] == {"const": RESULT_MANIFEST_SCHEMA}
        assert RESULT_MANIFEST_SCHEMA == "confflow.run_result_manifest.v1"
        assert run_result_schema_sha256() == canonical_sha256(schema)
        assert _envelope()["result_schema"] == schema

    def test_build_minimal_manifest(self) -> None:
        manifest = build_run_result_manifest(
            run_id="run-1",
            status="completed",
            definition_digest="sha256:" + "0" * 64,
            producer_version=PRODUCER_VERSION,
        )
        assert manifest["content_schema"] == RESULT_MANIFEST_SCHEMA
        assert manifest["steps"] == []
        assert manifest["analyses"] == []
        assert manifest["artifacts"] == []
        assert manifest["provenance"]["package"] == "confflow"

    def test_build_manifest_rejects_bad_status(self) -> None:
        try:
            build_run_result_manifest(
                run_id="run-1",
                status="exploded",
                definition_digest="sha256:" + "0" * 64,
                producer_version=PRODUCER_VERSION,
            )
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError("manifest accepted an unknown run status")


class TestValidation:
    def _recipe_yaml(self, recipe_id: str) -> bytes:
        catalog = build_recipe_catalog_v4()
        document = next(r for r in catalog["recipes"] if r["id"] == recipe_id)["document"]
        text: str = yaml.safe_dump(document)
        return text.encode("utf-8")

    def test_accepts_compilable_recipe(self) -> None:
        report = validate_workflow_bytes(self._recipe_yaml("optimize"))
        assert report.ok is True
        assert report.to_dict()["schema"] == CONFIGURATION_VALIDATION_SCHEMA
        assert report.step_ids == ("optimize",)
        assert report.definition_digest is not None

    def test_rejects_unknown_executor_structured(self) -> None:
        document = {
            "schema": SCHEMA_ID,
            "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
            "steps": [
                {
                    "id": "evil",
                    "executor": "no_such_executor",
                    "bindings": {"structure": {"source": {"run": "structures"}}},
                    "calculation": {
                        "program": "orca",
                        "native": {"keyword": "B3LYP"},
                        "recovery": {"profile": "none"},
                    },
                }
            ],
        }
        report = validate_workflow_bytes(yaml.safe_dump(document).encode("utf-8"))
        assert report.ok is False
        assert report.errors(), "expected structured error diagnostics"
        for diagnostic in report.diagnostics:
            assert set(diagnostic) >= {"code", "severity", "step_id", "field_path", "message"}

    def test_never_bare_tracebacks(self) -> None:
        bad_inputs = [
            b"\xff\xfe not utf-8 \x00",
            b"",
            b"::: not yaml ::: [",
            b"just a string",
            b"123",
            b"schema: confflow.workflow.v4\nsteps: []\n",
        ]
        for payload in bad_inputs:
            report = validate_workflow_bytes(payload)
            assert report.ok is False
            assert report.errors()
            for diagnostic in report.diagnostics:
                assert "Traceback" not in diagnostic["message"]

    def test_canonical_json_output_deterministic(self) -> None:
        report = validate_workflow_bytes(self._recipe_yaml("frequency"))
        assert report.to_canonical_json() == report.to_canonical_json()
        assert json.loads(report.to_canonical_json())["ok"] is True


class TestCrossRepoSelfVerify:
    """generate -> re-parse -> digest check -> recipe compiles -> validator accepts."""

    def test_contract_bytes_verify_end_to_end(self) -> None:
        raw = generate_contract_bytes(producer_version=PRODUCER_VERSION)
        envelope = json.loads(raw.decode("utf-8"))
        assert envelope["content_schema"] == CONFIGURATION_CONTRACT_V4_SCHEMA
        assert envelope["contract_digest"] == contract_digest_of(envelope)
        for name in ("workflow_schema", "editor_manifest", "recipe_catalog", "result_schema"):
            assert envelope[f"{name}_sha256"] == canonical_sha256(envelope[name]), name
        recipe = next(r for r in envelope["recipe_catalog"]["recipes"] if r["id"] == "tspes")
        compiled = compile_workflow(recipe["document"])
        assert compiled.ok, [str(d) for d in compiled.diagnostics]
        assert compiled.plan is not None
        report = validate_workflow_bytes(yaml.safe_dump(recipe["document"]).encode("utf-8"))
        assert report.ok is True
        assert tuple(sorted(report.step_ids)) == tuple(
            sorted(step["id"] for step in recipe["document"]["steps"])
        )
