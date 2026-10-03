#!/usr/bin/env python3

"""P0 PR-1 authoring seam tests (resource presence + thin producer projection).

These tests pin the two PR-1 deliverables:

- the resource/scheduler presence fix: an absent step field inherits the run
  global instead of being defaulted at parse time, and effective values
  resolve through the existing ``with_defaults`` authority;
- the ``confflow.producer.authoring`` seam: describe/candidates/instantiate/
  validate projected over the existing V4 authority, with the conservative
  auto-wire decision and no duplicated rule tables.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from confflow.domain.binding import SourceKind
from confflow.domain.canonical import canonical_sha256
from confflow.execution.registry import default_registry
from confflow.producer.authoring import (
    AUTHORING_OPERATIONS,
    binding_candidates,
    describe_step,
    dispatch_request,
    instantiate_card,
    validate_document,
)
from confflow.producer.boundary import authoring_protocol_schema
from confflow.producer.recipes import get_recipe_v4
from confflow.producer.validation import validate_workflow_bytes
from confflow.workflow.v4 import compile_workflow
from confflow.workflow.v4.document import SCHEMA_ID

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_DIR = REPO_ROOT / "docs" / "internal" / "fixtures" / "p0_boundary"

GLOBAL_RESOURCES = {"cores_per_item": 8, "memory_per_item": "16GiB"}
GLOBAL_SCHEDULER = {"max_parallel_items": 2}


# ----------------------------------------------------------------------
# Document builders (real registry vocabulary only)
# ----------------------------------------------------------------------


def calc_step(
    step_id: str,
    *,
    program: str = "orca",
    role: str = "opt",
    keyword: str = "B3LYP D3BJ Opt",
    adapter: str = "standard",
    bindings: dict[str, Any] | None = None,
    enabled: bool | None = None,
    resources: dict[str, Any] | None = None,
    scheduler: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one calculation step mapping."""
    step: dict[str, Any] = {
        "id": step_id,
        "executor": "calculation",
        "calculation": {
            "program": program,
            "role": role,
            "execution_adapter": adapter,
            "native": {"keyword": keyword},
        },
        "bindings": dict(bindings or {}),
    }
    if enabled is not None:
        step["enabled"] = enabled
    if resources is not None:
        step["resources"] = dict(resources)
    if scheduler is not None:
        step["scheduler"] = dict(scheduler)
    return step


def analysis_step(step_id: str, *, bindings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build one analysis step mapping."""
    return {
        "id": step_id,
        "executor": "analysis",
        "analysis": {"checks": [], "native": {}},
        "bindings": dict(bindings or {}),
    }


def confgen_step(step_id: str, *, bindings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build one conformer-generation step mapping."""
    return {
        "id": step_id,
        "executor": "confgen",
        "confgen": {
            "schema_version": 3,
            "torsions": [
                {
                    "id": "t1",
                    "bond": [2, 3],
                    "model": "relative_rotation_grid",
                    "angles": [0, 120, 240],
                    "treatment": "enumerate",
                }
            ],
            "seed": 42,
        },
        "bindings": dict(bindings or {}),
    }


def geometry_document(*, steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return a document with a named structure input and a checkpoint input."""
    return {
        "schema": SCHEMA_ID,
        "inputs": {
            "structures": {
                "kind": "structure",
                "cardinality": "many",
                "grouping": "each_entity",
            },
            "restart": {
                "kind": "artifact",
                "cardinality": "many",
                "role": "checkpoint",
            },
        },
        "global": {
            "scientific_defaults": {"charge": 0, "multiplicity": 1},
            "resources": dict(GLOBAL_RESOURCES),
            "scheduler": dict(GLOBAL_SCHEDULER),
        },
        "steps": (
            steps
            if steps is not None
            else [
                calc_step("opt_1", bindings={"structure": {"source": {"run": "structures"}}}),
                calc_step("sp_1", role="sp", keyword="B3LYP D3BJ SP"),
            ]
        ),
    }


def apply_binding(
    document: dict[str, Any], step_id: str, port: str, binding: dict[str, Any]
) -> dict[str, Any]:
    """Return a copy of *document* with one binding applied."""
    trial = json.loads(json.dumps(document))
    for step in trial["steps"]:
        if step["id"] == step_id:
            step.setdefault("bindings", {})[port] = binding
    return trial


def candidate_for(results: list[dict[str, Any]], source: dict[str, Any]) -> dict[str, Any]:
    """Return the single candidate carrying *source*."""
    matches = [item for item in results if item["source"] == source]
    assert len(matches) == 1, results
    return matches[0]


def assert_response_conforms(response: dict[str, Any]) -> None:
    """Validate a response against the frozen authoring response schema."""
    Draft202012Validator(authoring_protocol_schema()["response"]).validate(response)
    assert response["content_schema"] == "confflow.authoring.v4"
    assert response["operation"] in AUTHORING_OPERATIONS
    assert isinstance(response["ok"], bool)


# ----------------------------------------------------------------------
# 1-2: geometry candidates from run inputs and previous step outputs
# ----------------------------------------------------------------------


class TestGeometryCandidates:
    def test_geometry_candidate_from_named_run_input(self) -> None:
        document = geometry_document(steps=[calc_step("sp_1", role="sp", keyword="B3LYP D3BJ SP")])
        response = binding_candidates(
            document,
            "sp_1",
            "structure",
            action="add",
            expected_document_digest=dispatch_digest(document),
        )
        assert_response_conforms(response)
        assert response["ok"] is True
        candidate = candidate_for(response["result"], {"run": "structures"})
        assert candidate["source_kind"] == "run_input"
        assert candidate["compatibility"] == "compatible"
        assert candidate["pairing"] == "per_structure"
        assert candidate["effective_cardinality"] == "one"
        assert candidate["auto_wire"] is True, candidate["auto_wire_reasons"]
        assert candidate["request_document_digest"] == dispatch_digest(document)

    def test_geometry_candidate_from_previous_step_output(self) -> None:
        document = geometry_document(
            steps=[
                calc_step("opt_1", bindings={"structure": {"source": {"run": "structures"}}}),
                calc_step("sp_1", role="sp", keyword="B3LYP D3BJ SP"),
            ]
        )
        response = binding_candidates(document, "sp_1", "structure")
        candidate = candidate_for(response["result"], {"step": "opt_1", "port": "structures"})
        assert candidate["source_kind"] == "step_output"
        assert candidate["compatibility"] == "compatible"
        # The candidate facts must equal the real compiled edge.
        bound = apply_binding(
            document, "sp_1", "structure", {"source": {"step": "opt_1", "port": "structures"}}
        )
        compiled = compile_workflow(bound)
        assert compiled.ok is True and compiled.plan is not None
        edge = next(edge for edge in compiled.plan.graph.edges if edge.target_step_id == "sp_1")
        assert edge.source.step_id == "opt_1"
        assert candidate["pairing"] == edge.pairing.value
        assert candidate["effective_cardinality"] == edge.cardinality.value


# ----------------------------------------------------------------------
# 3: checkpoint and geometry from different sources
# ----------------------------------------------------------------------


class TestDifferentSources:
    def test_checkpoint_and_geometry_come_from_different_sources(self) -> None:
        document = geometry_document(
            steps=[
                calc_step("opt_1", bindings={"structure": {"source": {"run": "structures"}}}),
                calc_step(
                    "sp_1",
                    role="sp",
                    keyword="B3LYP D3BJ SP",
                    bindings={
                        "structure": {"source": {"step": "opt_1", "port": "structures"}},
                        "checkpoint": {
                            "source": {"run": "restart", "select": {"role": "checkpoint"}}
                        },
                    },
                ),
            ]
        )
        compiled = compile_workflow(document)
        assert compiled.ok is True
        described = describe_step(document, "sp_1")
        assert described["ok"] is True
        bindings = described["result"]["bindings"]
        assert bindings["structure"]["source"] == {
            "step": "opt_1",
            "port": "structures",
        }
        assert bindings["checkpoint"]["source"] == {
            "run": "restart",
            "select": {"role": "checkpoint"},
        }
        candidates = binding_candidates(document, "sp_1", "checkpoint")["result"]
        sources = [item["source"] for item in candidates]
        assert {"run": "restart"} in sources
        assert {"step": "opt_1", "port": "artifacts"} in sources


# ----------------------------------------------------------------------
# 4: an analysis step is never a geometry source
# ----------------------------------------------------------------------


class TestAnalysisIsNotGeometry:
    def test_analysis_step_is_not_offered_as_geometry_source(self) -> None:
        registry = default_registry()
        analysis_ports = registry.resolve_executor("analysis").output_ports
        assert all(port.kind.value != "structure" for port in analysis_ports)
        document = geometry_document(
            steps=[
                calc_step("opt_1", bindings={"structure": {"source": {"run": "structures"}}}),
                analysis_step(
                    "analysis_1",
                    bindings={"structures": {"source": {"step": "opt_1", "port": "structures"}}},
                ),
                calc_step("sp_1", role="sp", keyword="B3LYP D3BJ SP"),
            ]
        )
        response = binding_candidates(document, "sp_1", "structure")
        assert response["ok"] is True
        step_sources = [
            item["source"]["step"]
            for item in response["result"]
            if item["source_kind"] == "step_output"
        ]
        assert "analysis_1" not in step_sources
        assert "opt_1" in step_sources


# ----------------------------------------------------------------------
# 5: named reactant/product/guess via real recipes and registry facts
# ----------------------------------------------------------------------


class TestNamedStructures:
    def test_named_reactant_product_guess_use_real_recipe_metadata(self) -> None:
        qst3 = get_recipe_v4("qst3")["document"]
        response = binding_candidates(qst3, "qst3", "reactant")
        assert response["ok"] is True
        reactant = candidate_for(response["result"], {"run": "reactants"})
        assert reactant["pairing"] == "by_group_key"
        assert reactant["effective_cardinality"] == "one"
        assert reactant["compatibility"] == "compatible"
        # The qst3 recipe declares the guess input, so the guess port is offered.
        guess_response = binding_candidates(qst3, "qst3", "guess")
        guess = candidate_for(guess_response["result"], {"run": "guesses"})
        assert guess["pairing"] == "by_group_key"
        assert guess["effective_cardinality"] == "optional"
        # The qst2 recipe declares no guess input: no invented source exists.
        qst2 = get_recipe_v4("qst2")["document"]
        qst2_sources = [
            item["source"] for item in binding_candidates(qst2, "qst2", "guess")["result"]
        ]
        assert {"run": "guesses"} not in qst2_sources

    def test_group_key_ports_never_auto_wire_without_explicit_grouping(self) -> None:
        qst3 = get_recipe_v4("qst3")["document"]
        digest = dispatch_digest(qst3)
        response = binding_candidates(
            qst3,
            "qst3",
            "reactant",
            action="add",
            expected_document_digest=digest,
        )
        candidate = candidate_for(response["result"], {"run": "reactants"})
        assert candidate["auto_wire"] is False
        assert "external_grouping_guess_required" in candidate["auto_wire_reasons"]


# ----------------------------------------------------------------------
# 6-7: collection fan-out and cardinality/pairing metadata
# ----------------------------------------------------------------------


class TestCollectionFanOut:
    def test_collection_output_candidate_fans_out_per_structure(self) -> None:
        document = geometry_document(
            steps=[
                confgen_step("cg_1", bindings={"structure": {"source": {"run": "structures"}}}),
                calc_step("sp_1", role="sp", keyword="B3LYP D3BJ SP"),
            ]
        )
        candidate = candidate_for(
            binding_candidates(document, "sp_1", "structure")["result"],
            {"step": "cg_1", "port": "structures"},
        )
        # Fan-out is expressed by the per-structure pairing; the effective
        # cardinality is the target port's per-work-item contract, exactly as
        # the compiled graph resolves it.
        assert candidate["pairing"] == "per_structure"
        assert candidate["effective_cardinality"] == "one"
        source_ports = describe_step(document, "cg_1")["result"]["output_ports"]
        source_port = next(port for port in source_ports if port["name"] == "structures")
        assert source_port["cardinality"] == "many"
        assert source_port["pairing"] == "per_structure"
        bound = apply_binding(
            document, "sp_1", "structure", {"source": {"step": "cg_1", "port": "structures"}}
        )
        compiled = compile_workflow(bound)
        assert compiled.ok is True and compiled.plan is not None
        edge = next(edge for edge in compiled.plan.graph.edges if edge.target_step_id == "sp_1")
        assert edge.cardinality.value == candidate["effective_cardinality"]
        assert edge.pairing.value == candidate["pairing"]

    def test_cardinality_and_pairing_match_the_compiled_graph(self) -> None:
        document = geometry_document()
        candidates = binding_candidates(document, "sp_1", "structure")["result"]
        assert candidates, "expected at least one geometry candidate"
        bound = apply_binding(
            document,
            "sp_1",
            "structure",
            {"source": {"step": "opt_1", "port": "structures"}},
        )
        compiled = compile_workflow(bound)
        assert compiled.ok is True and compiled.plan is not None
        edge = next(edge for edge in compiled.plan.graph.edges if edge.target_step_id == "sp_1")
        candidate = candidate_for(candidates, {"step": "opt_1", "port": "structures"})
        assert candidate["pairing"] == edge.pairing.value
        assert candidate["effective_cardinality"] == edge.cardinality.value
        assert edge.source.kind is SourceKind.STEP_OUTPUT


# ----------------------------------------------------------------------
# 8: ambiguous candidates never auto-wire
# ----------------------------------------------------------------------


class TestAmbiguity:
    def test_two_legal_sources_disable_auto_wire(self) -> None:
        document = geometry_document()
        digest = dispatch_digest(document)
        response = binding_candidates(
            document,
            "sp_1",
            "structure",
            action="add",
            expected_document_digest=digest,
        )
        compatible = [item for item in response["result"] if item["compatibility"] == "compatible"]
        assert len(compatible) == 2
        assert all(item["auto_wire"] is False for item in compatible)
        assert all("source_not_unique" in item["auto_wire_reasons"] for item in compatible)

    def test_multi_role_artifact_source_needs_an_explicit_selector(self) -> None:
        document = geometry_document(
            steps=[
                calc_step("opt_1", bindings={"structure": {"source": {"run": "structures"}}}),
                calc_step("sp_1", role="sp", keyword="B3LYP D3BJ SP"),
            ]
        )
        # Drop the artifact run input so opt_1.artifacts is the only source for
        # the checkpoint target port; its multi-role contract requires a role.
        document["inputs"].pop("restart")
        document = apply_binding(
            document,
            "sp_1",
            "structure",
            {"source": {"step": "opt_1", "port": "structures"}},
        )
        digest = dispatch_digest(document)
        response = binding_candidates(
            document,
            "sp_1",
            "checkpoint",
            action="add",
            expected_document_digest=digest,
        )
        assert response["ok"] is True
        candidate = candidate_for(response["result"], {"step": "opt_1", "port": "artifacts"})
        assert candidate["compatibility"] == "needs_revalidation"
        assert candidate["auto_wire"] is False
        assert "selector_or_pairing_ambiguity" in candidate["auto_wire_reasons"]
        reasons = {item.get("reason") for item in candidate["diagnostics"]}
        assert "role_required" in reasons


# ----------------------------------------------------------------------
# 9: disabled/invalid/cycle sources are excluded
# ----------------------------------------------------------------------


class TestExcludedSources:
    def test_disabled_invalid_and_cycle_sources_are_excluded(self) -> None:
        document = geometry_document(
            steps=[
                calc_step("opt_1", bindings={"structure": {"source": {"run": "structures"}}}),
                calc_step(
                    "off_1",
                    bindings={"structure": {"source": {"run": "structures"}}},
                    enabled=False,
                ),
                {
                    "id": "bad_1",
                    "executor": "not_a_capability",
                    "bindings": {},
                },
                calc_step(
                    "sp_1",
                    role="sp",
                    keyword="B3LYP D3BJ SP",
                    bindings={"structure": {"source": {"step": "opt_1", "port": "structures"}}},
                ),
            ]
        )
        response = binding_candidates(document, "opt_1", "structure")
        sources = [item["source"] for item in response["result"]]
        assert {"run": "structures"} in sources
        # off_1 is disabled; bad_1 has no resolvable contract; sp_1 consumes
        # opt_1, so wiring opt_1 from it would create a cycle.
        assert not any(item.get("step") == "off_1" for item in sources)
        assert not any(item.get("step") == "bad_1" for item in sources)
        assert not any(item.get("step") == "sp_1" for item in sources)


# ----------------------------------------------------------------------
# 10-12: resource presence, inheritance, and scheduler independence
# ----------------------------------------------------------------------


class TestResourcePresence:
    @staticmethod
    @pytest.fixture(scope="class")
    def fixture_document() -> dict[str, Any]:
        payload = json.loads(
            (FIXTURE_DIR / "named_binding_workflow.json").read_text(encoding="utf-8")
        )
        import yaml

        return yaml.safe_load(payload["workflow_yaml"])

    def test_partial_override_resolves_against_the_global(
        self, fixture_document: dict[str, Any]
    ) -> None:
        parsed = compile_workflow(fixture_document)
        assert parsed.ok is True and parsed.plan is not None
        step = parsed.plan.step("sp_1")
        assert step is not None
        assert step.resources.cores_per_item == 8
        assert step.resources.memory_per_item_bytes == 32 * 1024**3
        assert step.scheduler.max_parallel_items == 2
        # Presence is preserved in the canonical definition.
        validated_step = parsed.plan.graph.step("sp_1")
        assert validated_step is not None
        assert validated_step.definition.resources.cores_per_item is None
        assert validated_step.definition.resources.memory_per_item_bytes == 32 * 1024**3
        assert validated_step.definition.scheduler.max_parallel_items is None

    def test_describe_reports_absent_fields_as_absent(
        self, fixture_document: dict[str, Any]
    ) -> None:
        described = describe_step(fixture_document, "opt_1")
        assert described["ok"] is True
        assert described["result"]["resources"]["declared"] == {}
        assert described["result"]["resources"]["absent"] == [
            "cores_per_item",
            "memory_per_item",
        ]
        assert described["result"]["resources"]["effective"] == {
            "cores_per_item": 8,
            "memory_per_item_bytes": 16 * 1024**3,
        }
        described_sp = describe_step(fixture_document, "sp_1")
        assert described_sp["result"]["resources"]["declared"] == {"memory_per_item": "32GiB"}
        assert described_sp["result"]["resources"]["absent"] == ["cores_per_item"]
        assert described_sp["result"]["resources"]["effective"] == {
            "cores_per_item": 8,
            "memory_per_item_bytes": 32 * 1024**3,
        }

    def test_clearing_the_override_restores_the_global(
        self, fixture_document: dict[str, Any]
    ) -> None:
        cleared = json.loads(json.dumps(fixture_document))
        for step in cleared["steps"]:
            if step["id"] == "sp_1":
                step.pop("resources")
        compiled = compile_workflow(cleared)
        assert compiled.ok is True and compiled.plan is not None
        step = compiled.plan.step("sp_1")
        assert step is not None
        assert step.resources.cores_per_item == 8
        assert step.resources.memory_per_item_bytes == 16 * 1024**3
        assert step.scheduler.max_parallel_items == 2

    def test_scheduler_is_independent_from_resources(self) -> None:
        document = geometry_document(
            steps=[
                calc_step("opt_1", bindings={"structure": {"source": {"run": "structures"}}}),
                calc_step(
                    "sp_1",
                    role="sp",
                    keyword="B3LYP D3BJ SP",
                    bindings={"structure": {"source": {"step": "opt_1", "port": "structures"}}},
                ),
            ]
        )
        for step in document["steps"]:
            if step["id"] == "sp_1":
                step["resources"] = {"memory_per_item": "32GiB"}
                step["scheduler"] = {"max_parallel_items": 4}
        compiled = compile_workflow(document)
        assert compiled.ok is True and compiled.plan is not None
        step = compiled.plan.step("sp_1")
        assert step is not None
        assert step.resources.cores_per_item == 8
        assert step.resources.memory_per_item_bytes == 32 * 1024**3
        assert step.scheduler.max_parallel_items == 4
        other = compiled.plan.step("opt_1")
        assert other is not None
        assert other.scheduler.max_parallel_items == 2
        # Overriding only scheduler leaves resources inherited.
        for step in document["steps"]:
            if step["id"] == "sp_1":
                del step["resources"]
        recompiled = compile_workflow(document)
        assert recompiled.ok is True and recompiled.plan is not None
        resolved = recompiled.plan.step("sp_1")
        assert resolved is not None
        assert resolved.resources.memory_per_item_bytes == 16 * 1024**3
        assert resolved.scheduler.max_parallel_items == 4


# ----------------------------------------------------------------------
# instantiate_card / validate_document
# ----------------------------------------------------------------------


class TestInstantiateCard:
    def test_instantiate_builds_and_validates_a_new_step(self) -> None:
        context = geometry_document(
            steps=[calc_step("opt_1", bindings={"structure": {"source": {"run": "structures"}}})]
        )
        snapshot = calc_step("card", role="sp", keyword="B3LYP D3BJ SP")
        snapshot.pop("bindings")
        response = instantiate_card(
            snapshot,
            "sp_new",
            context,
            {"structure": {"run": "structures"}},
        )
        assert_response_conforms(response)
        assert response["ok"] is True
        result = response["result"]
        assert result["step_id"] == "sp_new"
        assert result["allocated"] is False
        assert result["step"]["calculation"]["program"] == "orca"
        assert result["step"]["bindings"]["structure"] == {"source": {"run": "structures"}}
        assert result["validation"]["ok"] is True
        assert result["definition_digest"].startswith("sha256:")
        assert result["document_digest"].startswith("sha256:")
        assert compile_workflow(result["document"]).ok is True

    def test_instantiate_allocates_a_fresh_id_on_conflict(self) -> None:
        context = apply_binding(
            geometry_document(),
            "sp_1",
            "structure",
            {"source": {"step": "opt_1", "port": "structures"}},
        )
        snapshot = calc_step("card", role="sp", keyword="B3LYP D3BJ SP")
        snapshot.pop("bindings")
        response = instantiate_card(snapshot, "sp_1", context, {"structure": {"run": "structures"}})
        assert response["ok"] is True
        assert response["result"]["allocated"] is True
        assert response["result"]["step_id"] == "sp_1_2"
        assert response["result"]["step_id"] in {
            step["id"] for step in response["result"]["document"]["steps"]
        }

    def test_instantiate_allocates_a_legal_id_when_none_is_requested(self) -> None:
        context = {
            "schema": SCHEMA_ID,
            "inputs": {
                "structures": {
                    "kind": "structure",
                    "cardinality": "many",
                    "grouping": "each_entity",
                }
            },
            "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
            "steps": [],
        }
        snapshot = calc_step("card", role="sp", keyword="B3LYP D3BJ SP")
        snapshot.pop("bindings")
        response = instantiate_card(snapshot, None, context, {"structure": {"run": "structures"}})
        assert response["ok"] is True
        assert response["result"]["step_id"] == "card"
        assert response["result"]["allocated"] is True

    def test_instantiate_falls_back_from_an_illegal_requested_id(self) -> None:
        context = {
            "schema": SCHEMA_ID,
            "inputs": {
                "structures": {
                    "kind": "structure",
                    "cardinality": "many",
                    "grouping": "each_entity",
                }
            },
            "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
            "steps": [],
        }
        snapshot = calc_step("card", role="sp", keyword="B3LYP D3BJ SP")
        snapshot.pop("bindings")
        response = instantiate_card(
            snapshot, "9illegal", context, {"structure": {"run": "structures"}}
        )
        assert response["ok"] is True
        assert response["result"]["step_id"] == "card"
        assert response["result"]["allocated"] is True

    def test_instantiate_requires_a_snapshot(self) -> None:
        response = instantiate_card(None, "sp_new", None, None)
        assert response["ok"] is False
        reasons = {item.get("reason") for item in response["diagnostics"]}
        assert "snapshot_invalid" in reasons

    def test_instantiate_rejects_a_malformed_snapshot(self) -> None:
        response = instantiate_card({"id": "card"}, "sp_new", None, None)
        assert response["ok"] is False
        assert response["result"] is None
        reasons = {item.get("reason") for item in response["diagnostics"]}
        assert "snapshot_invalid" in reasons

    def test_instantiate_rejects_a_malformed_binding_choice(self) -> None:
        snapshot = calc_step("card", role="sp", keyword="B3LYP D3BJ SP")
        snapshot.pop("bindings")
        response = instantiate_card(snapshot, "sp_new", None, {"structure": 42})
        assert response["ok"] is False
        reasons = {item.get("reason") for item in response["diagnostics"]}
        assert "binding_choice_invalid" in reasons


class TestValidateDocument:
    def test_validate_document_wraps_the_producer_validator(self) -> None:
        document = geometry_document()
        payload = json.dumps(document).encode("utf-8")
        expected = validate_workflow_bytes(payload)
        response = validate_document(payload)
        assert_response_conforms(response)
        assert response["ok"] is expected.ok
        assert response["result"] == expected.to_dict()
        assert response["request_document_digest"].startswith("sha256:")

    def test_invalid_document_is_structured(self) -> None:
        response = validate_document(b"schema: nope\nsteps: []\n")
        assert response["ok"] is False
        assert response["result"]["ok"] is False
        assert response["diagnostics"]


# ----------------------------------------------------------------------
# Envelope dispatch + CLI
# ----------------------------------------------------------------------


def dispatch_digest(document: dict[str, Any]) -> str:
    """Return the seam's JCS digest of a request document."""
    return "sha256:" + canonical_sha256(document)


class TestDispatch:
    def test_dispatch_routes_every_operation(self) -> None:
        document = apply_binding(
            geometry_document(),
            "sp_1",
            "structure",
            {"source": {"step": "opt_1", "port": "structures"}},
        )
        digest = dispatch_digest(document)
        describe_request = {
            "content_schema": "confflow.authoring.v4",
            "operation": "describe_step",
            "document": document,
            "parameters": {"step_id": "sp_1"},
        }
        described = dispatch_request(json.dumps(describe_request).encode("utf-8"))
        assert described["ok"] is True
        assert described["operation"] == "describe_step"
        assert_response_conforms(described)

        candidate_request = {
            "content_schema": "confflow.authoring.v4",
            "operation": "binding_candidates",
            "document": document,
            "document_digest": digest,
            "parameters": {
                "target_step_id": "sp_1",
                "target_port": "structure",
                "action": "continue",
            },
        }
        candidates = dispatch_request(json.dumps(candidate_request).encode("utf-8"))
        assert candidates["ok"] is True
        assert candidates["request_document_digest"] == digest
        assert_response_conforms(candidates)

        validate_request = {
            "content_schema": "confflow.authoring.v4",
            "operation": "validate_document",
            "document": document,
        }
        validated = dispatch_request(json.dumps(validate_request).encode("utf-8"))
        assert validated["ok"] is True
        assert_response_conforms(validated)

    def test_dispatch_instantiate_card_rides_the_open_parameters_object(self) -> None:
        context = geometry_document(
            steps=[calc_step("opt_1", bindings={"structure": {"source": {"run": "structures"}}})]
        )
        snapshot = calc_step("card", role="sp", keyword="B3LYP D3BJ SP")
        snapshot.pop("bindings")
        request = {
            "content_schema": "confflow.authoring.v4",
            "operation": "instantiate_card",
            "document": context,
            "parameters": {
                "snapshot": snapshot,
                "requested_step_id": "sp_new",
                "binding_choices": {"structure": {"run": "structures"}},
            },
        }
        response = dispatch_request(json.dumps(request).encode("utf-8"))
        assert response["ok"] is True
        assert response["operation"] == "instantiate_card"
        assert response["result"]["step_id"] == "sp_new"
        assert_response_conforms(response)

    def test_malformed_requests_answer_structurally(self) -> None:
        not_json = dispatch_request(b"{")
        assert not_json["ok"] is False
        assert not_json["diagnostics"]
        bad_operation = dispatch_request(
            b'{"content_schema": "confflow.authoring.v4", "operation": "nope"}'
        )
        assert bad_operation["ok"] is False
        missing_document = dispatch_request(
            b'{"content_schema": "confflow.authoring.v4", "operation": "describe_step"}'
        )
        assert missing_document["ok"] is False

    def test_check_compatibility_is_deferred_not_silently_wrong(self) -> None:
        response = dispatch_request(
            b'{"content_schema": "confflow.authoring.v4", ' b'"operation": "check_compatibility"}'
        )
        assert response["ok"] is False
        reasons = {item.get("reason") for item in response["diagnostics"]}
        assert "unsupported_operation" in reasons


class TestAuthoringCli:
    def _run_cli(self, monkeypatch: pytest.MonkeyPatch, payload: bytes) -> tuple[int, str]:
        from confflow.v4cli import main

        stream = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
        monkeypatch.setattr(sys, "stdin", stream)
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)
        code = main(["authoring", "--stdin", "--json"])
        return code, captured.getvalue()

    def test_cli_dispatches_and_exits_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        document = geometry_document()
        request = {
            "content_schema": "confflow.authoring.v4",
            "operation": "describe_step",
            "document": document,
            "parameters": {"step_id": "opt_1"},
        }
        code, output = self._run_cli(monkeypatch, json.dumps(request).encode("utf-8"))
        assert code == 0
        envelope = json.loads(output)
        assert envelope["ok"] is True
        assert envelope["operation"] == "describe_step"

    def test_cli_bad_request_exits_one_with_envelope(self, monkeypatch: pytest.MonkeyPatch) -> None:
        code, output = self._run_cli(monkeypatch, b'{"operation": "describe_step"}')
        assert code == 1
        envelope = json.loads(output)
        assert envelope["ok"] is False
        assert envelope["diagnostics"]

    def test_cli_keeps_other_commands_unchanged(self) -> None:
        from confflow.v4cli import _build_parser

        parser = _build_parser()
        for argv, command in (
            (["contract", "--json"], "contract"),
            (["boundary", "--json"], "boundary"),
            (["canonical", "--stdin", "--json"], "canonical"),
            (["validate", "--stdin", "--json"], "validate"),
            (["authoring", "--stdin", "--json"], "authoring"),
        ):
            assert parser.parse_args(argv).command == command


# ----------------------------------------------------------------------
# 13: no duplicated rule tables in the authoring seam
# ----------------------------------------------------------------------


class TestNoDuplicatedRules:
    def test_authoring_module_has_no_rule_tables(self) -> None:
        import re

        import confflow.producer.authoring as authoring_mod

        source = Path(authoring_mod.__file__).read_text(encoding="utf-8")
        lowered = source.lower()
        for program_token in ("gaussian", "orca", "g16", "g09", "b3lyp", "def2"):
            assert re.search(rf"\b{program_token}\b", lowered) is None, program_token
        for port_token in (
            '"structure"',
            '"structures"',
            '"checkpoint"',
            '"reactant"',
            '"product"',
            '"guess"',
            '"artifacts"',
            '"results"',
        ):
            assert port_token not in source, port_token
        for vocabulary_token in (
            '"per_structure"',
            '"by_subject"',
            '"by_group_key"',
            '"one_or_more"',
        ):
            assert vocabulary_token not in source, vocabulary_token

    def test_authoring_module_reuses_the_shared_authorities(self) -> None:
        import confflow.producer.authoring as authoring_mod

        source = Path(authoring_mod.__file__).read_text(encoding="utf-8")
        for authority in (
            "resolve_executor_contract",
            "resolve_step_input_ports",
            "_resolve_run_policy",
            "_validate_step",
            "compile_workflow",
            "validate_workflow_bytes",
            "run_input_port_spec",
        ):
            assert authority in source, authority
