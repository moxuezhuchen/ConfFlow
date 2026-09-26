#!/usr/bin/env python3

"""V4-6 recipe catalog tests: every built-in recipe compiles, nothing hides."""

from __future__ import annotations

import copy
from typing import Any

import yaml

from confflow.config.canonical.recipes import RECIPE_CATALOG_SCHEMA
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

EXPECTED_IDS = (
    "optimize",
    "single_point",
    "frequency",
    "opt_freq",
    "transition_state",
    "irc",
    "qst2",
    "qst3",
    "neb",
    "goat",
    "tspes",
)

TSPES_IDS = (
    "ts",
    "ts_freq",
    "ts_sp",
    "irc",
    "endpoint_opt",
    "endpoint_freq",
    "endpoint_sp",
    "reaction_profile",
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
        assert get_recipe_v4("tspes")["id"] == "tspes"
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


class TestTspesChain:
    def test_tspes_is_the_real_chain(self) -> None:
        document = _by_id("tspes")["document"]
        assert [s["id"] for s in document["steps"]] == list(TSPES_IDS)
        by_id = {s["id"]: s for s in document["steps"]}
        assert by_id["ts"]["calculation"]["role"] == "ts"
        assert by_id["ts"]["calculation"]["check_params"] == {
            "imaginary_frequency_count": {"expected": 1}
        }
        for step_id in ("ts_freq", "ts_sp", "irc"):
            assert by_id[step_id]["bindings"] == {
                "structure": {"source": {"step": "ts", "port": "structures"}}
            }, step_id
        assert by_id["irc"]["calculation"]["result_profile"] == "path_endpoints"
        assert by_id["endpoint_opt"]["bindings"] == {
            "structure": {"source": {"step": "irc", "port": "structures"}}
        }
        assert by_id["endpoint_freq"]["bindings"] == {
            "structure": {"source": {"step": "endpoint_opt", "port": "structures"}}
        }
        assert by_id["endpoint_sp"]["bindings"] == {
            "structure": {"source": {"step": "endpoint_freq", "port": "structures"}}
        }
        analysis = by_id["reaction_profile"]
        assert analysis["executor"] == "analysis"
        assert analysis["bindings"] == {
            "structures": {"source": {"step": "irc", "port": "structures"}},
            "ts_structures": {"source": {"step": "ts", "port": "structures"}},
            "results": {"source": {"step": "endpoint_sp", "port": "results"}},
            "ts_results": {"source": {"step": "ts_freq", "port": "results"}},
        }
        assert analysis["analysis"]["native"] == {"method": "reaction_profile"}

    def test_tspes_compiles_to_eight_planned_steps(self) -> None:
        document = _by_id("tspes")["document"]
        compiled = compile_workflow(document)
        assert compiled.ok, [str(d) for d in compiled.diagnostics]
        assert compiled.plan is not None
        assert sorted(s.step_id for s in compiled.plan.steps) == sorted(TSPES_IDS)
