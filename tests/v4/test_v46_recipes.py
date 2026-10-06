#!/usr/bin/env python3

"""V4-6 recipe catalog tests: every built-in recipe compiles, nothing hides."""

from __future__ import annotations

import copy
from typing import Any

import yaml

from confflow.config.contract_schemas import RECIPE_CATALOG_SCHEMA
from confflow.domain.canonical import canonical_sha256
from confflow.producer.recipes import (
    RECIPE_IDS_V4,
    build_recipe_catalog_v4,
    get_recipe_v4,
    recipe_catalog_sha256_v4,
)
from confflow.producer.validation import validate_workflow_bytes
from confflow.workflow.v4.compiler import compile_workflow
from confflow.workflow.v4.document import SCHEMA_ID
from confflow.workflow.v4.parser import parse_workflow_document

# R2.2 声明：irc/qst2/qst3/neb/goat/tspes 六个 recipe 退役，目录剩 7 项。
EXPECTED_IDS = (
    "optimize",
    "single_point",
    "frequency",
    "opt_freq",
    "transition_state",
    "confgen_torsion",
    "monomer_conformers",
)


def _catalog() -> dict[str, Any]:
    return build_recipe_catalog_v4()


def _by_id(recipe_id: str) -> dict[str, Any]:
    return next(r for r in _catalog()["recipes"] if r["id"] == recipe_id)


class TestCatalogShape:
    def test_ids_in_frozen_order(self) -> None:
        assert RECIPE_IDS_V4 == EXPECTED_IDS
        assert [r["id"] for r in _catalog()["recipes"]] == list(EXPECTED_IDS)

    def test_envelope_names_v4_schema(self) -> None:
        catalog = _catalog()
        assert catalog["schema"] == RECIPE_CATALOG_SCHEMA
        assert catalog["workflow_schema_version"] == SCHEMA_ID
        assert SCHEMA_ID == "confflow.workflow.v4"

    def test_required_fields_are_a_subset_of_exposed(self) -> None:
        for recipe in _catalog()["recipes"]:
            assert recipe["required_fields"], recipe["id"]
            assert set(recipe["required_fields"]) <= set(recipe["exposed_fields"]), recipe["id"]

    def test_catalog_digest_covers_catalog_alone(self) -> None:
        assert recipe_catalog_sha256_v4() == canonical_sha256(_catalog())

    def test_catalog_is_isolated(self) -> None:
        first = _catalog()
        first["recipes"][0]["document"]["steps"][0]["id"] = "mutated"
        assert _by_id("optimize")["document"]["steps"][0]["id"] == "optimize"

    def test_get_recipe_v4_roundtrip(self) -> None:
        assert get_recipe_v4("optimize")["id"] == "optimize"
        try:
            get_recipe_v4("no_such_recipe")
        except KeyError:
            pass
        else:  # pragma: no cover
            raise AssertionError("get_recipe_v4 accepted an unknown recipe")


class TestRecipesCompile:
    def test_every_recipe_parses_and_compiles(self) -> None:
        for recipe in _catalog()["recipes"]:
            parsed = parse_workflow_document(recipe["document"])
            assert parsed.definition is not None, (
                recipe["id"],
                [str(d) for d in parsed.diagnostics],
            )
            compiled = compile_workflow(parsed)
            assert compiled.ok, (recipe["id"], [str(d) for d in compiled.diagnostics])
            assert compiled.plan is not None

    def test_every_recipe_validates_from_bytes(self) -> None:
        for recipe in _catalog()["recipes"]:
            payload = yaml.safe_dump(recipe["document"]).encode("utf-8")
            report = validate_workflow_bytes(payload)
            assert report.ok, (recipe["id"], [str(d) for d in report.diagnostics])

    def test_every_recipe_declares_the_scientific_fallback(self) -> None:
        # Native rendering requires resolved charge/multiplicity; the recipe
        # documents carry the neutral run-level fallback and expose the
        # per-step overrides so the user can replace it.
        for recipe in _catalog()["recipes"]:
            document = recipe["document"]
            assert document["global"]["scientific_defaults"] == {
                "charge": 0,
                "multiplicity": 1,
            }, recipe["id"]

    def test_uncompilable_recipe_fails(self) -> None:
        broken = copy.deepcopy(_by_id("optimize")["document"])
        broken["steps"][0]["executor"] = "no_such_executor"
        compiled = compile_workflow(broken)
        assert not compiled.ok
        assert [d for d in compiled.diagnostics if d.is_error]


class TestNoHiddenBehavior:
    def test_documents_carry_no_machine_config(self) -> None:
        for recipe in _catalog()["recipes"]:
            document = recipe["document"]
            assert set(document) <= {"schema", "inputs", "global", "steps"}, recipe["id"]
            assert document["schema"] == SCHEMA_ID
            assert document["steps"], recipe["id"]
            for step in document["steps"]:
                assert "resources" not in step, (recipe["id"], step["id"])
                assert "scheduler" not in step, (recipe["id"], step["id"])
                assert "execution" not in step, (recipe["id"], step["id"])

    def test_calculation_steps_pin_program_and_keyword(self) -> None:
        for recipe in _catalog()["recipes"]:
            for step in recipe["document"]["steps"]:
                if step.get("executor") != "calculation":
                    continue
                calculation = step["calculation"]
                assert calculation["program"], (recipe["id"], step["id"])
                assert isinstance(calculation["native"], dict), (recipe["id"], step["id"])
                assert calculation["native"].get("keyword"), (recipe["id"], step["id"])

    def test_step_ids_are_stable_and_unique(self) -> None:
        import re

        pattern = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
        for recipe in _catalog()["recipes"]:
            ids = [step["id"] for step in recipe["document"]["steps"]]
            assert len(set(ids)) == len(ids), recipe["id"]
            for step_id in ids:
                assert pattern.match(step_id), (recipe["id"], step_id)
