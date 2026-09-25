#!/usr/bin/env python3

"""Tests for the v3 configuration contract (R7 producer contract).

``v1`` and ``v2`` are frozen documents that already exist in the wild: the
assertions about them here are *regression* assertions proving the v3 addition
did not move them. ``v3`` advertises Workflow V3 (explicit DAG, stable step
ids, V3 editor manifest and recipe catalog) and is only ever built when a
caller explicitly asks for version 3.

Requirement map:

* R7-1: v1 unchanged (key set, schema id, digest, producer block).
* R7-2: v2 unchanged (v1 plus exactly the four added members).
* R7-3: v3 available via ``build_configuration_contract_for_version(3)`` and
  ``_BUILDERS``.
* R7-4: v3 advertises V3 parse/execute capabilities as True.
* R7-5: v3 digests equal the authoritative runtime schema digests. Digests are
  imported from the schema module, never hardcoded.
* R7-6: stable step-id grammar published and honoured.
* R7-7: explicit DAG semantics published and honoured by every recipe.
* R7-8: every V3 recipe document is schema-valid under the real V3 validator.
* R7-9: recipe and manifest builders are deterministic deepcopy-isolated copies.
* R7-10: ``opt_freq`` is an atomic single calc node.
* R7-11: ``conformer_search`` keeps ``confgen.chains`` in required fields.
* R7-12: unknown-extension runnable policy is ``error`` and enforced.
* R7-13: v3 editor fields cover the validator-backed calc/confgen fields.
* R7-14: the default builder still emits a v1 document old consumers can read.
* X4: the new contract digests equal the authoritative runtime schema digests.

Recipe validation helper choice: recipes are *templates* whose user-filled
fields (program/keyword/chains) are intentionally unpinned, so the real V3
validator :func:`validate_workflow_v3` is used with
``ValidationProfile.FRAGMENT`` (presence rules exempt), plus a structural
``Draft202012Validator`` check against the DOCUMENT profile (recipes carry
stable ids, so they must also be document-shaped). A RUNNABLE check is
asserted to *fail* on the unpinned fields, proving the ``required_fields``
mechanism is honest.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from confflow.config.canonical.contract import (
    _BUILDERS,
    CONFIGURATION_CONTRACT_BUILDERS,
    CONFIGURATION_CONTRACT_SCHEMA,
    CONFIGURATION_CONTRACT_V1_SCHEMA,
    CONFIGURATION_CONTRACT_V2_SCHEMA,
    CONFIGURATION_CONTRACT_V3_SCHEMA,
    CONFIGURATION_VALIDATION_SCHEMA,
    build_configuration_contract,
    build_configuration_contract_for_version,
    build_configuration_contract_v1,
    build_configuration_contract_v2,
    build_configuration_contract_v3,
)
from confflow.config.canonical.editor_manifest import (
    build_editor_manifest,
    build_editor_manifest_v3,
    editor_manifest_sha256_v3,
)
from confflow.config.canonical.execution_versions import CAPABILITIES, can_execute, can_parse
from confflow.config.canonical.extensions import (
    DEFAULT_EXTENSION_REGISTRY,
    EXTENSION_NAMESPACE_PATTERN,
)
from confflow.config.canonical.recipes import build_recipe_catalog_v3, recipe_catalog_sha256_v3
from confflow.config.canonical.schema import (
    WORKFLOW_SCHEMA_VERSION,
    WORKFLOW_SCHEMA_VERSION_V3,
    WORKFLOW_V3_ID_PATTERN,
    SchemaProfile,
    workflow_fragment_schema_sha256_v3,
    workflow_json_schema_v3,
    workflow_schema_sha256,
    workflow_schema_sha256_v3,
)
from confflow.config.canonical.serialization import canonical_sha256
from confflow.config.canonical.validation import ValidationProfile, validate_workflow_v3

#: The frozen v1 member set (mirrors test_configuration_contract_v2.py).
EXPECTED_V1_KEYS = {
    "producer",
    "schema",
    "validation_response_schema",
    "workflow_schema",
    "workflow_schema_sha256",
    "workflow_schema_version",
}

#: The members v2 adds. Nothing else may differ between v1 and v2.
EXPECTED_V2_ONLY_KEYS = {
    "editor_manifest",
    "editor_manifest_sha256",
    "recipe_catalog",
    "recipe_catalog_sha256",
}

#: Tokens that would prove runtime internals leaked into the producer contract.
FORBIDDEN_CONTRACT_TOKENS = (
    "WorkflowStateV2",
    "ExecutableIdentity",
    "SQLite",
    "sqlite",
    "fingerprint",
    "Binding",
    "binding",
    "step_layout",
    "steps layout",
)


def _v1() -> dict[str, Any]:
    return build_configuration_contract_v1(
        producer_version="2.1.6", producer_commit=None, producer_dirty=None
    )


def _v2() -> dict[str, Any]:
    return build_configuration_contract_v2(
        producer_version="2.1.6", producer_commit=None, producer_dirty=None
    )


def _v3() -> dict[str, Any]:
    return build_configuration_contract_v3(
        producer_version="2.1.6", producer_commit=None, producer_dirty=None
    )


def _v3_recipes() -> list[dict[str, Any]]:
    return build_recipe_catalog_v3()["recipes"]


def _recipe(recipe_id: str) -> dict[str, Any]:
    for recipe in _v3_recipes():
        if recipe["id"] == recipe_id:
            return recipe
    raise AssertionError(f"unknown V3 recipe: {recipe_id!r}")


class TestV1Unchanged:
    """R7-1: the v3 addition did not move the frozen v1 document."""

    def test_key_set_is_unchanged(self) -> None:
        assert set(_v1()) == EXPECTED_V1_KEYS

    def test_schema_id_is_unchanged(self) -> None:
        assert _v1()["schema"] == CONFIGURATION_CONTRACT_V1_SCHEMA
        assert CONFIGURATION_CONTRACT_V1_SCHEMA == "confflow.configuration-contract.v1"

    def test_workflow_digest_still_tracks_the_v2_schema_module(self) -> None:
        assert _v1()["workflow_schema_version"] == WORKFLOW_SCHEMA_VERSION
        assert _v1()["workflow_schema_sha256"] == workflow_schema_sha256()

    def test_producer_block_is_unchanged(self) -> None:
        assert _v1()["producer"] == {
            "package": "confflow",
            "version": "2.1.6",
            "commit": None,
            "dirty": None,
        }


class TestV2Unchanged:
    """R7-2: the v3 addition did not move the v2 document either."""

    def test_it_is_v1_plus_exactly_the_four_added_members(self) -> None:
        assert set(_v2()) == EXPECTED_V1_KEYS | EXPECTED_V2_ONLY_KEYS

    def test_every_shared_member_is_identical_to_v1(self) -> None:
        v1, v2 = _v1(), _v2()
        for key in EXPECTED_V1_KEYS - {"schema"}:
            assert v2[key] == v1[key], key
        assert v2["schema"] == CONFIGURATION_CONTRACT_V2_SCHEMA

    def test_it_does_not_advertise_v3(self) -> None:
        assert _v2()["workflow_schema_version"] == WORKFLOW_SCHEMA_VERSION
        assert _v2()["workflow_schema_version"] != WORKFLOW_SCHEMA_VERSION_V3


class TestV3Dispatch:
    """R7-3: v3 is available explicitly and only explicitly."""

    def test_schema_id(self) -> None:
        assert CONFIGURATION_CONTRACT_V3_SCHEMA == "confflow.configuration-contract.v3"
        assert _v3()["schema"] == CONFIGURATION_CONTRACT_V3_SCHEMA

    def test_for_version_dispatches_to_the_v3_builder(self) -> None:
        assert build_configuration_contract_for_version(3, producer_version="2.1.6") == _v3()

    def test_builders_alias_exposes_all_three_versions(self) -> None:
        assert _BUILDERS is CONFIGURATION_CONTRACT_BUILDERS
        assert sorted(_BUILDERS) == [1, 2, 3]
        assert _BUILDERS[3] is build_configuration_contract_v3

    def test_for_version_still_dispatches_v1_and_v2(self) -> None:
        assert build_configuration_contract_for_version(1, producer_version="2.1.6") == _v1()
        assert build_configuration_contract_for_version(2, producer_version="2.1.6") == _v2()

    @pytest.mark.parametrize("version", [0, 4, 99, -1])
    def test_an_unsupported_version_raises(self, version: int) -> None:
        with pytest.raises(ValueError, match="unsupported configuration contract version"):
            build_configuration_contract_for_version(version, producer_version="2.1.6")


class TestV3Capabilities:
    """R7-4: the contract advertises V3 parse/execute as True."""

    def test_capabilities_are_true(self) -> None:
        assert _v3()["capabilities"] == {"parse": True, "execute": True}

    def test_capabilities_match_the_runtime_capability_table(self) -> None:
        capability = CAPABILITIES[WORKFLOW_SCHEMA_VERSION_V3]
        assert capability.parse is True
        assert capability.execute is True
        assert can_parse(WORKFLOW_SCHEMA_VERSION_V3) is True
        assert can_execute(WORKFLOW_SCHEMA_VERSION_V3) is True
        assert _v3()["workflow_capabilities"]["parse"] == capability.parse
        assert _v3()["workflow_capabilities"]["execute"] == capability.execute

    def test_workflow_capabilities_names_the_v3_schema(self) -> None:
        assert _v3()["workflow_capabilities"]["schema"] == WORKFLOW_SCHEMA_VERSION_V3
        assert _v3()["workflow_schema_version"] == WORKFLOW_SCHEMA_VERSION_V3


class TestV3Digests:
    """R7-5: every embedded digest recomputes from the artifact it describes."""

    def test_document_schema_digest_recomputes(self) -> None:
        document = _v3()
        assert document["workflow_schema"] == workflow_json_schema_v3(SchemaProfile.DOCUMENT)
        assert document["workflow_schema_sha256"] == workflow_schema_sha256_v3()
        assert document["workflow_schema_sha256"] == canonical_sha256(document["workflow_schema"])

    def test_fragment_schema_digest_recomputes(self) -> None:
        document = _v3()
        assert document["workflow_fragment_schema"] == workflow_json_schema_v3(
            SchemaProfile.FRAGMENT
        )
        assert document["workflow_fragment_schema_sha256"] == (workflow_fragment_schema_sha256_v3())
        assert document["workflow_fragment_schema_sha256"] == canonical_sha256(
            document["workflow_fragment_schema"]
        )

    def test_manifest_digest_recomputes(self) -> None:
        document = _v3()
        assert document["editor_manifest"] == build_editor_manifest_v3()
        assert document["editor_manifest_sha256"] == editor_manifest_sha256_v3()
        assert document["editor_manifest_sha256"] == canonical_sha256(document["editor_manifest"])

    def test_catalog_digest_recomputes(self) -> None:
        document = _v3()
        assert document["recipe_catalog"] == build_recipe_catalog_v3()
        assert document["recipe_catalog_sha256"] == recipe_catalog_sha256_v3()
        assert document["recipe_catalog_sha256"] == canonical_sha256(document["recipe_catalog"])

    def test_every_digest_is_a_hex_sha256(self) -> None:
        document = _v3()
        for key in (
            "workflow_schema_sha256",
            "workflow_fragment_schema_sha256",
            "editor_manifest_sha256",
            "recipe_catalog_sha256",
        ):
            digest = document[key]
            assert isinstance(digest, str) and len(digest) == 64
            assert digest == digest.lower()
            int(digest, 16)


class TestAuthoritativeDigestsX4:
    """X4: the new contract digests equal the authoritative runtime schema digests."""

    def test_document_digest_is_the_runtime_digest(self) -> None:
        assert _v3()["workflow_schema_sha256"] == workflow_schema_sha256_v3()
        assert _v3()["workflow_capabilities"]["document_schema_sha256"] == (
            workflow_schema_sha256_v3()
        )

    def test_fragment_digest_is_the_runtime_digest(self) -> None:
        assert _v3()["workflow_fragment_schema_sha256"] == workflow_fragment_schema_sha256_v3()
        assert _v3()["workflow_capabilities"]["fragment_schema_sha256"] == (
            workflow_fragment_schema_sha256_v3()
        )

    def test_manifest_digest_is_the_runtime_digest(self) -> None:
        assert _v3()["editor_manifest_sha256"] == editor_manifest_sha256_v3()

    def test_catalog_digest_is_the_runtime_digest(self) -> None:
        assert _v3()["recipe_catalog_sha256"] == recipe_catalog_sha256_v3()


class TestStableIdGrammar:
    """R7-6: the contract publishes the stable step-id grammar."""

    def test_id_pattern_is_the_runtime_grammar(self) -> None:
        assert _v3()["step_grammar"]["id_pattern"] == WORKFLOW_V3_ID_PATTERN
        assert _v3()["step_identity"]["id_pattern"] == WORKFLOW_V3_ID_PATTERN
        assert _v3()["dag_semantics"]["id_pattern"] == WORKFLOW_V3_ID_PATTERN

    def test_stable_ids_match_and_unstable_ids_do_not(self) -> None:
        assert re.fullmatch(WORKFLOW_V3_ID_PATTERN, "s001")
        assert re.fullmatch(WORKFLOW_V3_ID_PATTERN, "opt")
        for bad in ("S001", "1abc", "has space", "s-001", ""):
            assert re.fullmatch(WORKFLOW_V3_ID_PATTERN, bad) is None, bad

    def test_labels_are_display_only(self) -> None:
        assert _v3()["step_grammar"]["labels"] == "display_only"
        assert _v3()["step_identity"]["labels_display_only"] is True
        assert _v3()["dag_semantics"]["labels_display_only"] is True


class TestDagSemantics:
    """R7-7: explicit DAG semantics published and honoured by every recipe."""

    def test_contract_declares_explicit_inputs(self) -> None:
        assert _v3()["dag_semantics"]["explicit_inputs"] is True
        assert _v3()["dag_semantics"]["inputs_required"] is True
        assert _v3()["step_grammar"]["inputs"] == "explicit_array"

    def test_every_recipe_step_carries_an_explicit_inputs_array(self) -> None:
        for recipe in _v3_recipes():
            for step in recipe["document"]["steps"]:
                assert isinstance(step["inputs"], list), (recipe["id"], step)
                assert re.fullmatch(WORKFLOW_V3_ID_PATTERN, step["id"]), step
                for predecessor in step["inputs"]:
                    assert re.fullmatch(WORKFLOW_V3_ID_PATTERN, predecessor), step


class TestRecipesSchemaValid:
    """R7-8: every V3 recipe document validates with the real V3 validator."""

    def test_each_recipe_declares_the_v3_schema(self) -> None:
        for recipe in _v3_recipes():
            assert recipe["document"]["schema"] == WORKFLOW_SCHEMA_VERSION_V3, recipe["id"]

    def test_each_recipe_is_document_shaped(self) -> None:
        validator = Draft202012Validator(workflow_json_schema_v3(SchemaProfile.DOCUMENT))
        for recipe in _v3_recipes():
            errors = list(validator.iter_errors(recipe["document"]))
            assert errors == [], (recipe["id"], [e.message for e in errors])

    def test_each_recipe_passes_the_real_v3_fragment_validator(self) -> None:
        for recipe in _v3_recipes():
            diagnostics = validate_workflow_v3(
                recipe["document"], profile=ValidationProfile.FRAGMENT
            )
            assert diagnostics == [], (
                recipe["id"],
                [(d.code, d.path, d.message) for d in diagnostics],
            )

    def test_runnable_validation_flags_only_the_unpinned_user_fields(self) -> None:
        """Templates pin intent, not user choices: RUNNABLE must ask for them."""
        for recipe in _v3_recipes():
            diagnostics = validate_workflow_v3(
                recipe["document"], profile=ValidationProfile.RUNNABLE
            )
            assert diagnostics != [], recipe["id"]

    def test_expected_recipe_ids_are_present(self) -> None:
        assert sorted(recipe["id"] for recipe in _v3_recipes()) == [
            "conformer_search",
            "opt_freq",
            "optimize",
            "single_point",
        ]


class TestRecipesDeterministic:
    """R7-9: builders hand out isolated copies; mutation cannot leak."""

    def test_two_builds_are_identical(self) -> None:
        assert build_recipe_catalog_v3() == build_recipe_catalog_v3()
        assert build_editor_manifest_v3() == build_editor_manifest_v3()
        assert _v3() == _v3()

    def test_mutating_a_catalog_copy_leaves_the_next_build_untouched(self) -> None:
        first = build_recipe_catalog_v3()
        first["recipes"].append({"id": "evil"})
        first["recipes"][0]["document"]["steps"].append({"id": "evil"})
        fresh = build_recipe_catalog_v3()
        assert all(recipe["id"] != "evil" for recipe in fresh["recipes"])
        assert all(
            all(step.get("id") != "evil" for step in recipe["document"]["steps"])
            for recipe in fresh["recipes"]
        )
        assert fresh["recipes"][0]["document"]["steps"] == [
            {
                "id": "s001",
                "label": "Optimize",
                "type": "calc",
                "inputs": [],
                "params": {"itask": "opt"},
            }
        ]

    def test_mutating_a_manifest_copy_leaves_the_next_build_untouched(self) -> None:
        first = build_editor_manifest_v3()
        first["fields"].append({"field_id": "evil"})
        assert all(field["field_id"] != "evil" for field in build_editor_manifest_v3()["fields"])
        assert build_editor_manifest_v3() == build_editor_manifest_v3()


class TestOptFreqAtomic:
    """R7-10: ``opt_freq`` stays one atomic calc node, never a split pair."""

    def test_single_calc_node_with_atomic_task(self) -> None:
        recipe = _recipe("opt_freq")
        steps = recipe["document"]["steps"]
        assert len(steps) == 1
        assert steps[0]["type"] == "calc"
        assert steps[0]["id"] == "s001"
        assert steps[0]["inputs"] == []
        assert steps[0]["params"] == {"itask": "opt_freq"}


class TestConformerRequiredFields:
    """R7-11: recipe required fields name the user-filled editor fields."""

    def test_conformer_search_requires_chains(self) -> None:
        assert "confgen.chains" in _recipe("conformer_search")["required_fields"]

    def test_calc_recipes_require_program_and_keyword(self) -> None:
        for recipe_id in ("optimize", "opt_freq", "single_point"):
            required = _recipe(recipe_id)["required_fields"]
            assert "calc.program" in required, recipe_id
            assert "calc.keyword" in required, recipe_id

    def test_required_fields_are_a_subset_of_exposed_fields(self) -> None:
        for recipe in _v3_recipes():
            assert set(recipe["required_fields"]) <= set(recipe["exposed_fields"]), recipe["id"]


class TestUnknownExtensionPolicy:
    """R7-12: unknown extensions are runnable-policy errors, not silent data."""

    def test_contract_publishes_the_error_policy(self) -> None:
        document = _v3()
        assert document["unknown_extension_runnable_policy"] == "error"
        assert document["extensions"]["unknown_extension_runnable_policy"] == "error"
        assert document["extensions"]["unknown_runnable_policy"] == "error"

    def test_known_namespaces_and_grammar_come_from_the_registry(self) -> None:
        document = _v3()
        assert document["extensions"]["known_namespaces"] == sorted(
            DEFAULT_EXTENSION_REGISTRY.namespaces()
        )
        assert document["extensions"]["namespace_pattern"] == EXTENSION_NAMESPACE_PATTERN
        assert document["known_semantic_extensions"] == sorted(
            DEFAULT_EXTENSION_REGISTRY.namespaces()
        )

    def test_runnable_validation_rejects_an_unknown_namespace(self) -> None:
        raw: dict[str, Any] = {
            "schema": WORKFLOW_SCHEMA_VERSION_V3,
            "global": {},
            "steps": [
                {
                    "id": "s001",
                    "label": "x",
                    "type": "calc",
                    "inputs": [],
                    "params": {"itask": "opt", "iprog": "g16", "keyword": "B3LYP"},
                }
            ],
            "extensions": {"unknown.ns": {"a": 1}},
        }
        diagnostics = validate_workflow_v3(raw, profile=ValidationProfile.RUNNABLE)
        assert any(d.code == "workflow.v3.extension_unknown" for d in diagnostics)

    def test_fragment_validation_preserves_an_unknown_namespace(self) -> None:
        raw: dict[str, Any] = {
            "schema": WORKFLOW_SCHEMA_VERSION_V3,
            "global": {},
            "steps": [
                {
                    "id": "s001",
                    "label": "x",
                    "type": "calc",
                    "inputs": [],
                    "params": {"itask": "opt"},
                }
            ],
            "extensions": {"unknown.ns": {"a": 1}},
        }
        diagnostics = validate_workflow_v3(raw, profile=ValidationProfile.FRAGMENT)
        assert all(d.code != "workflow.v3.extension_unknown" for d in diagnostics)


class TestEditorFields:
    """R7-13: the v3 editor manifest covers the validator-backed fields."""

    def test_validator_backed_fields_are_present(self) -> None:
        field_ids = {field["field_id"] for field in build_editor_manifest_v3()["fields"]}
        for expected in (
            "calc.program",
            "calc.task",
            "calc.keyword",
            "calc.checkpoint_from",
            "confgen.chains",
            "confgen.angle_step",
        ):
            assert expected in field_ids, expected

    def test_v3_is_the_v1_field_set_plus_checkpoint_from(self) -> None:
        v1_ids = {field["field_id"] for field in build_editor_manifest()["fields"]}
        v3_ids = {field["field_id"] for field in build_editor_manifest_v3()["fields"]}
        assert v1_ids <= v3_ids
        assert "calc.checkpoint_from" in v3_ids - v1_ids

    def test_checkpoint_from_points_at_the_v3_checkpoint_member(self) -> None:
        fields = {field["field_id"]: field for field in build_editor_manifest_v3()["fields"]}
        pointer = fields["calc.checkpoint_from"]["json_pointer"]
        assert pointer == "/steps/{index}/checkpoint/from_step"

    def test_manifest_names_the_v3_workflow_schema(self) -> None:
        assert build_editor_manifest_v3()["workflow_schema_version"] == WORKFLOW_SCHEMA_VERSION_V3

    def test_contract_manifest_matches_the_builder(self) -> None:
        assert _v3()["editor_manifest"] == build_editor_manifest_v3()


class TestOldConsumerDefault:
    """R7-14: the default builder still emits v1 for existing consumers."""

    def test_default_is_v1(self) -> None:
        assert build_configuration_contract(producer_version="2.1.6") == _v1()

    def test_historical_alias_still_means_v1(self) -> None:
        assert CONFIGURATION_CONTRACT_SCHEMA == CONFIGURATION_CONTRACT_V1_SCHEMA
        assert CONFIGURATION_CONTRACT_V2_SCHEMA == "confflow.configuration-contract.v2"
        assert CONFIGURATION_VALIDATION_SCHEMA == "confflow.configuration-validation.v1"


class TestNoRuntimeLeaks:
    """No runtime internals may appear anywhere in the v3 contract."""

    def test_serialized_contract_contains_no_forbidden_token(self) -> None:
        text = json.dumps(_v3())
        lowered = text.lower()
        for token in FORBIDDEN_CONTRACT_TOKENS:
            if token.lower() == token:
                assert token not in lowered, token
            else:
                assert token not in text, token

    def test_v1_and_v2_default_docs_have_no_v3_members(self) -> None:
        for name in ("capabilities", "dag_semantics", "step_grammar", "step_identity"):
            assert name not in _v1(), name
            assert name not in _v2(), name
