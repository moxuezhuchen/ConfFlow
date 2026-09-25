#!/usr/bin/env python3

"""Tests for the v3 configuration contract and the V3 authoring surface.

``v1`` and ``v2`` are frozen documents that already exist in the wild: the
assertions about them here are *regression* assertions proving the v3 work did
not move them. ``v3`` advertises Workflow V3 and is only ever built when a
caller explicitly asks for version 3.

Requirement map:

* R7-1: v1 unchanged (key set, schema id, digest, producer block).
* R7-2: v2 unchanged (v1 plus exactly the four added members).
* R7-3: v3 available via ``build_configuration_contract_for_version(3)`` and
  ``CONFIGURATION_CONTRACT_BUILDERS``; ``_BUILDERS`` is not public.
* R7-4: v3 advertises V3 parse/execute capabilities as True (``workflow`` block).
* R7-5: v3 digests equal the authoritative runtime schema digests. Digests are
  imported from the schema module, never hardcoded.
* R7-6: stable step-id grammar published; the opaque generator pattern is
  narrower (``s001`` is legal per the grammar but never generated).
* R7-7: explicit DAG semantics published and honoured by every recipe.
* R7-8 (RCP1): every V3 recipe document is a FRAGMENT, never a document.
* R7-9: recipe and manifest builders are deterministic deepcopy-isolated copies.
* R7-10 (RCP10): ``opt_freq`` is an atomic single calc node.
* R7-11 (RCP11): ``conformer_search`` keeps ``confgen.chains`` in required fields.
* R7-12: unknown-extension runnable policy is ``error`` and enforced.
* R7-13 / E1-E6: V1/V2 index pointers frozen; V3 ID selectors; no index
  identity; theory fields exposed; checkpoint by stable id; choices derived
  from the registries.
* R7-14: the default builder still emits a v1 document old consumers can read.
* X4: the new contract digests equal the authoritative runtime schema digests.
* S45-46: one authoritative ``workflow`` block; no ``step_grammar`` /
  ``step_identity`` / ``dag_semantics`` triple, no duplicated extension keys,
  no ``_BUILDERS`` export.
* S49: the contract exposes the V3 schema + digests, ID grammar + generator
  policy, step selector, explicit DAG, checkpoint ref, nonsemantic annotations,
  extension policy, structured theory, raw keyword, program/task choices,
  recipe fragments + instantiate/rebase rule, parse + execute.
* RCP2: no recipe retains a final ``s001`` identity (no step ``id`` at all).
* RCP12: required fields are a subset of exposed fields.

Allocator mechanics (RCP3-RCP9: opaque shape, collision redraw, exhaustion,
disjoint instantiations, DAG/checkpoint rebase, explicit attach) live in
``tests/test_recipe_instantiate_v3.py``.

Recipe validation helper choice: recipes are *templates* whose user-filled
fields (program/keyword/chains) are intentionally unpinned, so the real V3
validator :func:`validate_workflow_v3` is used with
``ValidationProfile.FRAGMENT`` (presence rules exempt). A RUNNABLE check on the
raw template is asserted to *fail*, proving the ``required_fields`` mechanism
is honest; filling those fields and instantiating through
:func:`instantiate_recipe_v3` is asserted to validate clean.
"""

from __future__ import annotations

import importlib
import json
import random
import re
from typing import Any, get_args

import pytest
from jsonschema import Draft202012Validator

import confflow.config.canonical as canonical
from confflow.config.canonical.contract import (
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
    EDITOR_MANIFEST_SCHEMA,
    build_editor_manifest,
    build_editor_manifest_v3,
    editor_manifest_sha256_v3,
    program_choices,
    task_choices,
    theory_dispersion_choices,
    theory_solvent_models,
)
from confflow.config.canonical.execution_versions import CAPABILITIES, can_execute, can_parse
from confflow.config.canonical.extensions import (
    DEFAULT_EXTENSION_REGISTRY,
    EXTENSION_NAMESPACE_PATTERN,
)
from confflow.config.canonical.recipes import (
    RECIPE_STEP_ID_PATTERN,
    STEP_ID_ALPHABET,
    STEP_ID_MAX_ATTEMPTS,
    STEP_ID_SUFFIX_LENGTH,
    build_recipe_catalog_v3,
    instantiate_recipe_v3,
    recipe_catalog_sha256_v3,
)
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
from confflow.config.canonical.structured import (
    STRUCTURED_THEORY_FIELDS,
    compile_structured_calc,
)
from confflow.config.canonical.theory import PROGRAM_CAPABILITIES, TheorySpec
from confflow.config.canonical.types import ProgramName, TaskName
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

#: The exact v3 top-level member set: one spelling of each fact, no duplicates.
EXPECTED_V3_KEYS = {
    "producer",
    "schema",
    "validation_response_schema",
    "workflow_schema",
    "workflow_schema_sha256",
    "workflow_schema_version",
    "workflow_fragment_schema",
    "workflow_fragment_schema_sha256",
    "workflow",
    "structured_theory",
    "recipes",
    "editor_manifest",
    "editor_manifest_sha256",
    "recipe_catalog",
    "recipe_catalog_sha256",
}

#: Removed spellings from the prior closure; none may reappear on v3.
REMOVED_V3_KEYS = {
    "capabilities",
    "workflow_capabilities",
    "step_grammar",
    "step_identity",
    "dag_semantics",
    "known_semantic_extensions",
    "unknown_extension_runnable_policy",
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


def _resolve_dotted(path: str) -> Any:
    module_name, attribute = path.rsplit(".", 1)
    return getattr(importlib.import_module(module_name), attribute)


class TestV1Unchanged:
    """R7-1: the v3 work did not move the frozen v1 document."""

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
    """R7-2: the v3 work did not move the v2 document either."""

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

    def test_builders_table_exposes_all_three_versions(self) -> None:
        assert sorted(CONFIGURATION_CONTRACT_BUILDERS) == [1, 2, 3]
        assert CONFIGURATION_CONTRACT_BUILDERS[3] is build_configuration_contract_v3

    def test_private_alias_is_not_public(self) -> None:
        import confflow.config.canonical.contract as contract_module

        assert "_BUILDERS" not in contract_module.__all__
        assert "_BUILDERS" not in canonical.__all__

    def test_for_version_still_dispatches_v1_and_v2(self) -> None:
        assert build_configuration_contract_for_version(1, producer_version="2.1.6") == _v1()
        assert build_configuration_contract_for_version(2, producer_version="2.1.6") == _v2()

    @pytest.mark.parametrize("version", [0, 4, 99, -1])
    def test_an_unsupported_version_raises(self, version: int) -> None:
        with pytest.raises(ValueError, match="unsupported configuration contract version"):
            build_configuration_contract_for_version(version, producer_version="2.1.6")


class TestV3TopLevel:
    """S45-46: one spelling of each fact on the v3 envelope."""

    def test_exact_key_set(self) -> None:
        assert set(_v3()) == EXPECTED_V3_KEYS

    def test_removed_duplicate_spellings_are_gone(self) -> None:
        document = _v3()
        for key in REMOVED_V3_KEYS:
            assert key not in document, key
        assert "unknown_runnable_policy" not in document


class TestV3WorkflowBlock:
    """S45-46/S49: the single authoritative workflow/identity/DAG structure."""

    def test_parse_and_execute_are_true_and_match_the_runtime_table(self) -> None:
        workflow = _v3()["workflow"]
        assert workflow["parse"] is True
        assert workflow["execute"] is True
        capability = CAPABILITIES[WORKFLOW_SCHEMA_VERSION_V3]
        assert (capability.parse, capability.execute) == (True, True)
        assert can_parse(WORKFLOW_SCHEMA_VERSION_V3) is True
        assert can_execute(WORKFLOW_SCHEMA_VERSION_V3) is True

    def test_workflow_block_names_the_v3_schema_and_digests(self) -> None:
        workflow = _v3()["workflow"]
        assert workflow["schema"] == WORKFLOW_SCHEMA_VERSION_V3
        assert _v3()["workflow_schema_version"] == WORKFLOW_SCHEMA_VERSION_V3
        assert workflow["document_schema_sha256"] == workflow_schema_sha256_v3()
        assert workflow["fragment_schema_sha256"] == workflow_fragment_schema_sha256_v3()

    def test_step_selector_is_the_stable_id(self) -> None:
        assert _v3()["workflow"]["step_selector"] == "id"

    def test_identity_grammar_labels_and_generator_policy(self) -> None:
        identity = _v3()["workflow"]["identity"]
        assert identity["grammar"] == WORKFLOW_V3_ID_PATTERN
        assert identity["labels_display_only"] is True
        generator = identity["generator"]
        assert generator["form"] == "opaque"
        assert generator["pattern"] == RECIPE_STEP_ID_PATTERN
        assert generator["alphabet"] == STEP_ID_ALPHABET
        assert generator["length"] == STEP_ID_SUFFIX_LENGTH
        assert generator["max_attempts"] == STEP_ID_MAX_ATTEMPTS
        assert generator["sequential_ids_never_issued"] is True

    def test_explicit_dag_semantics(self) -> None:
        dag = _v3()["workflow"]["dag"]
        assert dag == {"inputs": "explicit_array", "inputs_required": True}

    def test_checkpoint_is_a_stable_id_reference(self) -> None:
        checkpoint = _v3()["workflow"]["checkpoint"]
        assert checkpoint["from_step"] == "stable_step_id_ref"
        assert checkpoint["calc_only"] is True
        assert checkpoint["strict_ancestor_required"] is True

    def test_annotations_are_nonsemantic(self) -> None:
        assert _v3()["workflow"]["annotations"] == {"semantic": False}

    def test_single_extension_policy_block(self) -> None:
        extensions = _v3()["workflow"]["extensions"]
        assert extensions == {
            "known_namespaces": sorted(DEFAULT_EXTENSION_REGISTRY.namespaces()),
            "namespace_pattern": EXTENSION_NAMESPACE_PATTERN,
            "unknown_runnable_policy": "error",
        }


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
        assert _v3()["workflow"]["document_schema_sha256"] == workflow_schema_sha256_v3()

    def test_fragment_digest_is_the_runtime_digest(self) -> None:
        assert _v3()["workflow_fragment_schema_sha256"] == workflow_fragment_schema_sha256_v3()
        assert _v3()["workflow"]["fragment_schema_sha256"] == workflow_fragment_schema_sha256_v3()

    def test_manifest_digest_is_the_runtime_digest(self) -> None:
        assert _v3()["editor_manifest_sha256"] == editor_manifest_sha256_v3()

    def test_catalog_digest_is_the_runtime_digest(self) -> None:
        assert _v3()["recipe_catalog_sha256"] == recipe_catalog_sha256_v3()


class TestStableIdGrammar:
    """R7-6: the contract publishes the stable step-id grammar."""

    def test_workflow_identity_names_the_runtime_grammar(self) -> None:
        assert _v3()["workflow"]["identity"]["grammar"] == WORKFLOW_V3_ID_PATTERN

    def test_sequential_ids_are_legal_but_never_generated(self) -> None:
        assert re.fullmatch(WORKFLOW_V3_ID_PATTERN, "s001")
        assert re.fullmatch(WORKFLOW_V3_ID_PATTERN, "opt")
        assert re.fullmatch(RECIPE_STEP_ID_PATTERN, "s001") is None
        for bad in ("S001", "1abc", "has space", "s-001", ""):
            assert re.fullmatch(WORKFLOW_V3_ID_PATTERN, bad) is None, bad

    def test_labels_are_display_only(self) -> None:
        assert _v3()["workflow"]["identity"]["labels_display_only"] is True


class TestRecipesFragment:
    """R7-7/R7-8/RCP1/RCP2/RCP10/RCP11: recipes are fragments, not documents."""

    def test_contract_states_the_fragment_profile(self) -> None:
        assert _v3()["recipes"]["document_profile"] == "fragment"

    def test_each_recipe_declares_the_v3_schema(self) -> None:
        for recipe in _v3_recipes():
            assert recipe["document"]["schema"] == WORKFLOW_SCHEMA_VERSION_V3, recipe["id"]

    def test_no_recipe_step_carries_a_final_identity(self) -> None:
        for recipe in _v3_recipes():
            for step in recipe["document"]["steps"]:
                assert "id" not in step, (recipe["id"], step)

    def test_no_s001_survives_anywhere_in_the_v3_catalog(self) -> None:
        assert "s001" not in json.dumps(build_recipe_catalog_v3())

    def test_no_recipe_is_document_shaped(self) -> None:
        validator = Draft202012Validator(workflow_json_schema_v3(SchemaProfile.DOCUMENT))
        for recipe in _v3_recipes():
            errors = list(validator.iter_errors(recipe["document"]))
            assert errors != [], recipe["id"]

    def test_each_recipe_is_fragment_shaped(self) -> None:
        validator = Draft202012Validator(workflow_json_schema_v3(SchemaProfile.FRAGMENT))
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

    def test_every_recipe_step_carries_an_explicit_inputs_array(self) -> None:
        for recipe in _v3_recipes():
            for step in recipe["document"]["steps"]:
                assert isinstance(step["inputs"], list), (recipe["id"], step)
                for predecessor in step["inputs"]:
                    assert re.fullmatch(WORKFLOW_V3_ID_PATTERN, predecessor), step

    def test_runnable_validation_flags_the_unpinned_user_fields(self) -> None:
        """Templates pin intent, not user choices: RUNNABLE must ask for them."""
        for recipe in _v3_recipes():
            diagnostics = validate_workflow_v3(
                recipe["document"], profile=ValidationProfile.RUNNABLE
            )
            assert diagnostics != [], recipe["id"]

    def test_filling_required_fields_and_instantiating_is_runnable(self) -> None:
        """The authoring chain the contract promises: fill, instantiate, run."""
        recipe = _recipe("optimize")
        document = json.loads(json.dumps(recipe["document"]))
        compiled = compile_structured_calc(
            {"method": "B3LYP", "basis": "def2-SVP"}, program="g16", task="opt"
        )
        document["steps"][0]["params"].update(compiled)
        document["steps"] = instantiate_recipe_v3(
            document, existing_ids=(), attach_roots_to=None, rng=random.Random(7)
        )
        diagnostics = validate_workflow_v3(document, profile=ValidationProfile.RUNNABLE)
        assert diagnostics == [], [(d.code, d.path, d.message) for d in diagnostics]

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
            {"label": "Optimize", "type": "calc", "inputs": [], "params": {"itask": "opt"}}
        ]

    def test_mutating_a_manifest_copy_leaves_the_next_build_untouched(self) -> None:
        first = build_editor_manifest_v3()
        first["fields"].append({"field_id": "evil"})
        assert all(field["field_id"] != "evil" for field in build_editor_manifest_v3()["fields"])
        assert build_editor_manifest_v3() == build_editor_manifest_v3()


class TestOptFreqAtomic:
    """R7-10/RCP10: ``opt_freq`` stays one atomic calc node, never a split pair."""

    def test_single_calc_node_with_atomic_task(self) -> None:
        recipe = _recipe("opt_freq")
        steps = recipe["document"]["steps"]
        assert len(steps) == 1
        assert steps[0]["type"] == "calc"
        assert "id" not in steps[0]
        assert steps[0]["inputs"] == []
        assert steps[0]["params"] == {"itask": "opt_freq"}


class TestConformerRequiredFields:
    """R7-11/RCP11/RCP12: recipe required fields name the user-filled editor fields."""

    def test_conformer_search_requires_chains_and_pins_no_defaults(self) -> None:
        recipe = _recipe("conformer_search")
        assert recipe["required_fields"] == ["confgen.chains"]
        assert recipe["document"]["steps"][0]["params"] == {}

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

    def test_contract_publishes_the_single_error_policy(self) -> None:
        assert _v3()["workflow"]["extensions"]["unknown_runnable_policy"] == "error"

    def test_known_namespaces_and_grammar_come_from_the_registry(self) -> None:
        extensions = _v3()["workflow"]["extensions"]
        assert extensions["known_namespaces"] == sorted(DEFAULT_EXTENSION_REGISTRY.namespaces())
        assert extensions["namespace_pattern"] == EXTENSION_NAMESPACE_PATTERN

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
                    "type": "calc",
                    "inputs": [],
                    "params": {"itask": "opt"},
                }
            ],
            "extensions": {"unknown.ns": {"a": 1}},
        }
        diagnostics = validate_workflow_v3(raw, profile=ValidationProfile.FRAGMENT)
        assert all(d.code != "workflow.v3.extension_unknown" for d in diagnostics)


class TestEditorV3Selectors:
    """R7-13/E1-E6: V1/V2 index addressing frozen; V3 selects steps by id."""

    def _v1_fields(self) -> list[dict[str, Any]]:
        return build_editor_manifest()["fields"]

    def _v3_fields(self) -> list[dict[str, Any]]:
        return build_editor_manifest_v3()["fields"]

    def _v3_step_fields(self) -> list[dict[str, Any]]:
        return [field for field in self._v3_fields() if field["context"] != "global"]

    def test_v1_step_pointers_still_use_the_index_placeholder(self) -> None:
        """E1: the V1/V2 manifest spelling is frozen."""
        for field in self._v1_fields():
            pointer = field["json_pointer"]
            if field["context"] == "global":
                assert "{index}" not in pointer, field["field_id"]
                assert "step_selector" not in field, field["field_id"]
            else:
                assert "{index}" in pointer, field["field_id"]
                assert pointer.startswith("/steps/{index}/params/")
                assert "step_selector" not in field, field["field_id"]

    def test_v2_contract_embeds_the_index_addressed_manifest(self) -> None:
        """E1: the v2 contract's manifest is the untouched V1 shape."""
        manifest = _v2()["editor_manifest"]
        assert manifest == build_editor_manifest()
        assert manifest["workflow_schema_version"] == WORKFLOW_SCHEMA_VERSION
        for field in manifest["fields"]:
            if field["context"] != "global":
                assert "{index}" in field["json_pointer"], field["field_id"]

    def test_v3_step_fields_select_by_stable_id(self) -> None:
        """E2: every V3 step-scoped field targets a stable id, explicitly."""
        for field in self._v3_step_fields():
            assert "{id}" in field["json_pointer"], field["field_id"]
            assert field["step_selector"] == "id", field["field_id"]
            assert field["relative_pointer"] == field["json_pointer"].replace(
                "/steps/{id}", "", 1
            ), field["field_id"]

    def test_no_v3_field_depends_on_array_index_identity(self) -> None:
        """E3: index addressing is gone from V3; relative pointers stay unique."""
        for field in self._v3_fields():
            assert "{index}" not in field["json_pointer"], field["field_id"]
            assert "{index}" not in field.get("relative_pointer", ""), field["field_id"]
        pointers = [field["json_pointer"] for field in self._v3_fields()]
        assert len(set(pointers)) == len(pointers)
        relatives = [field["relative_pointer"] for field in self._v3_step_fields()]
        assert len(set(relatives)) == len(relatives)
        for field in self._v3_step_fields():
            assert field["relative_pointer"].startswith(("/params/", "/checkpoint/"))

    def test_global_v3_fields_carry_no_selector(self) -> None:
        for field in self._v3_fields():
            if field["context"] == "global":
                assert "step_selector" not in field, field["field_id"]
                assert "relative_pointer" not in field, field["field_id"]

    def test_structured_theory_fields_are_exposed_with_id_pointers(self) -> None:
        """E4: method/basis/dispersion/solvent are editable structured fields."""
        fields = {field["field_id"]: field for field in self._v3_fields()}
        assert fields["calc.theory.method"]["json_pointer"] == "/steps/{id}/params/theory/method"
        assert fields["calc.theory.basis"]["json_pointer"] == "/steps/{id}/params/theory/basis"
        assert (
            fields["calc.theory.dispersion"]["json_pointer"]
            == "/steps/{id}/params/theory/dispersion"
        )
        assert fields["calc.theory.solvent"]["json_pointer"] == "/steps/{id}/params/theory/solvent"
        for field_id in (
            "calc.theory.method",
            "calc.theory.basis",
            "calc.theory.dispersion",
            "calc.theory.solvent",
        ):
            assert fields[field_id]["step_selector"] == "id", field_id

    def test_no_second_program_or_task_field_exists(self) -> None:
        """E4: the GUI never faces two unrelated Program fields."""
        field_ids = {field["field_id"] for field in self._v3_fields()}
        assert "calc.theory.program" not in field_ids
        assert "calc.theory.task" not in field_ids
        fields = {field["field_id"]: field for field in self._v3_fields()}
        assert "theory.program" in fields["calc.program"]["description"]
        assert "theory.task" in fields["calc.task"]["description"]

    def test_checkpoint_from_points_at_a_stable_id(self) -> None:
        """E5: the checkpoint reference is a stable-id address, not an index."""
        fields = {field["field_id"]: field for field in self._v3_fields()}
        checkpoint = fields["calc.checkpoint_from"]
        assert checkpoint["json_pointer"] == "/steps/{id}/checkpoint/from_step"
        assert checkpoint["step_selector"] == "id"
        assert checkpoint["relative_pointer"] == "/checkpoint/from_step"

    def test_v3_is_the_v1_field_set_plus_theory_plus_checkpoint(self) -> None:
        v1_ids = {field["field_id"] for field in self._v1_fields()}
        v3_ids = {field["field_id"] for field in self._v3_fields()}
        assert v1_ids <= v3_ids
        assert v3_ids - v1_ids == {
            "calc.theory.method",
            "calc.theory.basis",
            "calc.theory.dispersion",
            "calc.theory.solvent",
            "calc.checkpoint_from",
        }

    def test_program_and_task_choices_come_from_the_literals(self) -> None:
        """E6: program/task choices are derived from the authoritative literals."""
        fields = {field["field_id"]: field for field in self._v3_fields()}
        assert [c["value"] for c in fields["calc.program"]["choices"]] == list(
            get_args(ProgramName)
        )
        assert [c["value"] for c in fields["calc.task"]["choices"]] == list(get_args(TaskName))
        assert program_choices() == fields["calc.program"]["choices"]
        assert task_choices() == fields["calc.task"]["choices"]

    def test_dispersion_choices_come_from_the_capability_registry(self) -> None:
        """E6: dispersion choices are the union of the registry's models."""
        fields = {field["field_id"]: field for field in self._v3_fields()}
        expected = sorted(
            {
                str(model).strip().lower()
                for capabilities in PROGRAM_CAPABILITIES.values()
                for model in capabilities.get("dispersion_models", ())
            }
        )
        assert [c["value"] for c in fields["calc.theory.dispersion"]["choices"]] == expected
        assert theory_dispersion_choices() == fields["calc.theory.dispersion"]["choices"]

    def test_solvent_models_come_from_the_capability_registry(self) -> None:
        """E6: the solvent description names the registry's models, not a copy."""
        fields = {field["field_id"]: field for field in self._v3_fields()}
        for model in theory_solvent_models():
            assert model in fields["calc.theory.solvent"]["description"], model
        expected = sorted(
            {
                str(model).strip().lower()
                for capabilities in PROGRAM_CAPABILITIES.values()
                for model in capabilities.get("solvent_models", ())
            }
        )
        assert theory_solvent_models() == expected

    def test_manifest_envelope_schema_stays_shared(self) -> None:
        """The V3 manifest reuses the shared envelope schema id."""
        # The workflow_schema_version member discriminates the addressing
        # family; see the editor_manifest module docstring.
        assert build_editor_manifest_v3()["schema"] == EDITOR_MANIFEST_SCHEMA
        assert build_editor_manifest()["schema"] == EDITOR_MANIFEST_SCHEMA
        assert build_editor_manifest_v3()["workflow_schema_version"] == WORKFLOW_SCHEMA_VERSION_V3

    def test_contract_manifest_matches_the_builder(self) -> None:
        assert _v3()["editor_manifest"] == build_editor_manifest_v3()


class TestStructuredTheoryBlock:
    """S49: the contract publishes the structured-theory vocabulary and mapping."""

    def test_fields_match_the_published_vocabulary(self) -> None:
        block = _v3()["structured_theory"]
        assert block["fields"] == list(STRUCTURED_THEORY_FIELDS)
        assert block["wire_member"] == "params.theory"

    def test_fields_match_what_the_theory_spec_accepts(self) -> None:
        probe = {
            "program": "g16",
            "task": "opt",
            "method": "B3LYP",
            "basis": "def2-SVP",
            "dispersion": "d3bj",
            "solvent": "water",
        }
        for field_name in _v3()["structured_theory"]["fields"]:
            TheorySpec.from_dict({field_name: probe[field_name]})
        with pytest.raises(ValueError, match="Unknown theory field"):
            TheorySpec.from_dict({"bogus": "x"})

    def test_single_program_task_mapping_is_stated(self) -> None:
        mapping = _v3()["structured_theory"]["program_task_mapping"]
        for token in ("iprog", "itask", "theory.program", "theory.task"):
            assert token in mapping, token

    def test_compiler_entry_point_resolves_to_the_producer_compiler(self) -> None:
        compiler = _resolve_dotted(_v3()["structured_theory"]["compiler"])
        assert compiler is compile_structured_calc

    def test_raw_keyword_escape_hatch_and_agreement_rule(self) -> None:
        keyword = _v3()["structured_theory"]["keyword"]
        assert keyword["member"] == "params.keyword"
        assert keyword["role"] == "verbatim_escape_hatch"
        assert keyword["consistency"] == "theory_and_keyword_must_agree"

    def test_choices_come_from_the_registries(self) -> None:
        block = _v3()["structured_theory"]
        assert [c["value"] for c in block["program_choices"]] == list(get_args(ProgramName))
        assert [c["value"] for c in block["task_choices"]] == list(get_args(TaskName))
        assert block["dispersion_choices"] == theory_dispersion_choices()
        assert block["solvent_models"] == theory_solvent_models()


class TestRecipesPolicyBlock:
    """S49/RCP: the contract states the fragment + instantiate/rebase rule."""

    def test_document_profile_is_fragment(self) -> None:
        assert _v3()["recipes"]["document_profile"] == "fragment"

    def test_required_fields_rule_names_the_editor_fields(self) -> None:
        rule = _v3()["recipes"]["required_fields"]
        for token in ("calc.program", "calc.keyword", "confgen.chains"):
            assert token in rule, token

    def test_instantiate_entry_point_resolves(self) -> None:
        instantiate = _resolve_dotted(_v3()["recipes"]["instantiate"])
        assert instantiate is instantiate_recipe_v3

    def test_rebase_rule_states_attach_semantics(self) -> None:
        rule = _v3()["recipes"]["rebase_rule"]
        assert "attach_roots_to" in rule
        assert "checkpoint.from_step" in rule


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
        for name in ("workflow", "structured_theory", "recipes"):
            assert name not in _v1(), name
            assert name not in _v2(), name
