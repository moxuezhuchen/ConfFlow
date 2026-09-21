#!/usr/bin/env python3

"""Producer-side consistency tests for the recipe catalog.

A recipe is a claim that "ConfFlow can be asked to do this".  These tests hold
that claim to two standards:

* the catalog is internally coherent (unique ids, labelled, no repeated field
  references, references that exist in the manifest);
* every recipe *document* is a real workflow: it validates against the published
  workflow JSON schema **and** parses through the canonical entry point
  (:func:`confflow.config.canonical.parser.parse_workflow_mapping`).  There is
  deliberately no recipe-only validator -- a private schema for recipes would be
  exactly the drift this whole contract exists to remove.

The last class is the one that protects user intent: a recipe must pin what makes
it a different recipe and nothing else, so it never materialises a producer
default into a document.
"""

from __future__ import annotations

import json
from typing import Any, get_args

import jsonschema
import pytest

from confflow.config.canonical.editor_manifest import build_editor_manifest
from confflow.config.canonical.parser import parse_workflow_mapping
from confflow.config.canonical.recipes import (
    RECIPE_CATALOG_SCHEMA,
    build_recipe_catalog,
    recipe_catalog_sha256,
)
from confflow.config.canonical.schema import workflow_json_schema
from confflow.config.canonical.serialization import canonical_json, canonical_sha256
from confflow.config.canonical.types import TaskName

EXPECTED_RECIPE_IDS = ["optimize", "opt_freq", "single_point", "conformer_search"]

#: The recipes whose document is one ``calc`` step.  ``conformer_search`` is
#: a ``confgen`` step instead, so every calc-specific assertion is scoped to
#: this subset rather than to the catalog as a whole.
CALC_RECIPE_IDS = ["optimize", "opt_freq", "single_point"]

#: ``params`` members that are a user's choice or a producer default.  A recipe
#: that wrote any of these would turn a default into an explicit override the
#: moment the user picked the recipe.
MUST_NOT_BE_PINNED = {
    "iprog",
    "keyword",
    "cores_per_task",
    "total_memory",
    "max_parallel_jobs",
    "charge",
    "multiplicity",
    "auto_clean",
    "enable_dynamic_resources",
    # ``chains`` is the user naming bonds in *their* molecule, so it is a choice
    # like ``keyword``: a recipe that pinned it would be describing one structure.
    "chains",
}


def _catalog() -> dict[str, Any]:
    return build_recipe_catalog()


def _recipes() -> list[dict[str, Any]]:
    return _catalog()["recipes"]


def _by_id(recipe_id: str) -> dict[str, Any]:
    return next(item for item in _recipes() if item["id"] == recipe_id)


def _step_params(recipe: dict[str, Any]) -> list[dict[str, Any]]:
    return [step.get("params", {}) for step in recipe["document"]["steps"]]


class TestEnvelope:
    def test_schema_and_label(self) -> None:
        catalog = _catalog()

        assert catalog["schema"] == RECIPE_CATALOG_SCHEMA
        assert catalog["label"].strip()

    def test_no_provenance_is_embedded(self) -> None:
        catalog = _catalog()

        assert "producer" not in catalog
        assert "contract_key" not in catalog

    def test_it_is_pure_data(self) -> None:
        text = json.dumps(_catalog(), ensure_ascii=False).lower()
        for token in ("pyside", "pyqt", "widget", "qcombobox"):
            assert token not in text


class TestDeterminism:
    def test_two_builds_are_identical(self) -> None:
        assert build_recipe_catalog() == build_recipe_catalog()
        assert canonical_json(build_recipe_catalog()) == canonical_json(build_recipe_catalog())

    def test_the_digest_is_the_digest_of_the_document(self) -> None:
        assert recipe_catalog_sha256() == canonical_sha256(build_recipe_catalog())
        assert recipe_catalog_sha256() == recipe_catalog_sha256()

    def test_a_returned_document_is_isolated_from_the_singleton(self) -> None:
        before = build_recipe_catalog()
        before["recipes"][0]["label"] = "tampered"
        before["recipes"][0]["document"]["steps"][0]["params"]["iprog"] = "orca"
        before["recipes"].append({"id": "injected"})

        after = build_recipe_catalog()
        assert after["recipes"][0]["label"] == "Optimize"
        assert "iprog" not in after["recipes"][0]["document"]["steps"][0]["params"]
        assert len(after["recipes"]) == len(EXPECTED_RECIPE_IDS)
        assert recipe_catalog_sha256() == canonical_sha256(after)


class TestCatalogConsistency:
    def test_recipe_ids_are_unique(self) -> None:
        ids = [item["id"] for item in _recipes()]
        assert len(set(ids)) == len(ids)

    def test_recipe_ids_are_the_expected_set(self) -> None:
        assert [item["id"] for item in _recipes()] == EXPECTED_RECIPE_IDS

    def test_labels_descriptions_and_categories_are_present(self) -> None:
        for item in _recipes():
            assert str(item["label"]).strip(), item["id"]
            assert str(item["description"]).strip(), item["id"]
            assert str(item["category"]).strip(), item["id"]

    def test_order_values_are_unique_and_sane(self) -> None:
        orders = [item["order"] for item in _recipes()]
        assert len(set(orders)) == len(orders)
        assert orders == sorted(orders)
        assert all(isinstance(item, int) and item >= 0 for item in orders)

    def test_required_fields_are_unique_within_a_recipe(self) -> None:
        for item in _recipes():
            required = item["required_fields"]
            assert len(set(required)) == len(required), item["id"]

    def test_exposed_fields_are_unique_within_a_recipe(self) -> None:
        for item in _recipes():
            exposed = item["exposed_fields"]
            assert len(set(exposed)) == len(exposed), item["id"]

    def test_required_and_exposed_fields_exist_in_the_manifest(self) -> None:
        known = {field["field_id"] for field in build_editor_manifest()["fields"]}
        for item in _recipes():
            for field_id in (*item["required_fields"], *item["exposed_fields"]):
                assert field_id in known, f"{item['id']} names unknown field {field_id}"

    def test_exposed_fields_cover_the_required_ones(self) -> None:
        """A required field the user cannot see would be an unfillable recipe."""
        for item in _recipes():
            assert set(item["required_fields"]) <= set(item["exposed_fields"]), item["id"]


class TestRecipeDocuments:
    @pytest.mark.parametrize("recipe_id", EXPECTED_RECIPE_IDS)
    def test_document_validates_against_the_published_workflow_schema(self, recipe_id: str) -> None:
        jsonschema.validate(_by_id(recipe_id)["document"], workflow_json_schema())

    @pytest.mark.parametrize("recipe_id", EXPECTED_RECIPE_IDS)
    def test_document_parses_through_the_canonical_entry_point(self, recipe_id: str) -> None:
        config = parse_workflow_mapping(_by_id(recipe_id)["document"])

        assert config.steps, recipe_id
        assert all(step.type in {"calc", "confgen"} for step in config.steps), recipe_id

    @pytest.mark.parametrize("recipe_id", CALC_RECIPE_IDS)
    def test_a_calc_recipe_is_a_single_calc_step(self, recipe_id: str) -> None:
        config = parse_workflow_mapping(_by_id(recipe_id)["document"])

        assert all(step.type == "calc" for step in config.steps), recipe_id

    def test_the_conformer_recipe_is_a_confgen_step(self) -> None:
        config = parse_workflow_mapping(_by_id("conformer_search")["document"])

        assert [step.type for step in config.steps] == ["confgen"]

    def test_every_document_carries_a_global_block_and_steps(self) -> None:
        for item in _recipes():
            document = item["document"]
            assert "global" in document, item["id"]
            assert isinstance(document["steps"], list) and document["steps"], item["id"]

    def test_consecutive_builds_share_nothing_mutable(self) -> None:
        first = build_recipe_catalog()
        second = build_recipe_catalog()

        assert first == second
        assert first is not second
        assert first["recipes"][0]["document"] is not second["recipes"][0]["document"]


class TestRecipeIntent:
    """A recipe pins intent, never a producer default."""

    def test_no_recipe_pins_a_default_or_a_user_choice(self) -> None:
        for item in _recipes():
            for params in _step_params(item):
                pinned = set(params) & MUST_NOT_BE_PINNED
                assert not pinned, f"{item['id']} pins {sorted(pinned)}"

    def test_each_calc_recipe_pins_exactly_its_task(self) -> None:
        for item in _recipes():
            if item["id"] not in CALC_RECIPE_IDS:
                continue
            params = _step_params(item)
            assert len(params) == 1, item["id"]
            assert set(params[0]) == {"itask"}, item["id"]

    def test_a_confgen_recipe_pins_nothing_at_all(self) -> None:
        """A confgen step's identity is its ``type``; there is no ``itask`` to pin.

        Pinning anything else here (``chains``, ``angle_step``) would bake either a
        user's choice or a producer default into the document, which is exactly what
        rule 2 of the module docstring forbids.
        """
        for item in _recipes():
            if item["id"] in CALC_RECIPE_IDS:
                continue
            for params in _step_params(item):
                assert params == {}, item["id"]

    def test_pinned_tasks_are_legal_tasks(self) -> None:
        legal = set(get_args(TaskName))
        for item in _recipes():
            for params in _step_params(item):
                if "itask" not in params:
                    continue
                assert params["itask"] in legal, item["id"]

    def test_the_pinned_task_is_the_one_the_label_promises(self) -> None:
        assert _step_params(_by_id("opt_freq"))[0]["itask"] == "opt_freq"
        assert _step_params(_by_id("optimize"))[0]["itask"] == "opt"
        assert _step_params(_by_id("single_point"))[0]["itask"] == "sp"

    def test_each_recipe_describes_exactly_one_step(self) -> None:
        for item in _recipes():
            assert len(item["document"]["steps"]) == 1, item["id"]

    @pytest.mark.parametrize(
        ("recipe_id", "step_name"),
        [
            ("optimize", "opt"),
            ("opt_freq", "opt_freq"),
            ("single_point", "sp"),
            ("conformer_search", "confgen"),
        ],
    )
    def test_the_step_name_is_the_expected_one(self, recipe_id: str, step_name: str) -> None:
        assert _by_id(recipe_id)["document"]["steps"][0]["name"] == step_name
