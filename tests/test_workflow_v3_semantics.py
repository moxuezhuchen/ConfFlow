#!/usr/bin/env python3

"""R3.4 — Workflow V3 semantic validation, authoritative graph and fingerprint.

Covers the authoritative id graph, checkpoint semantics, extension recognition,
the disabled-step profile-aware params, and the frozen definition fingerprint.
Nothing here touches V2 behaviour, and nothing starts V3 planning/execution.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from confflow.config.canonical import (
    CanonicalStepDefinition,
    CanonicalWorkflowDefinition,
    ExtensionRegistry,
    SchemaProfile,
    ValidationProfile,
    build_validated_graph,
    parse_v3_document,
    v3_id_order_key,
    validate_v3_definition,
    validate_workflow_v3,
    workflow_definition_fingerprint_v3,
)
from confflow.config.canonical.fingerprint import (
    _EXECUTION_CLASS_STEP_PARAMS,
    WorkflowFingerprintError,
    build_workflow_definition_payload_v3,
)
from confflow.config.canonical.issues import ConfigValidationError
from confflow.config.canonical.types import WorkflowConfig

V3 = "confflow.workflow.v3"


def _doc(steps: list[dict[str, Any]], **root: Any) -> dict[str, Any]:
    document: dict[str, Any] = {"schema": V3, "steps": steps}
    document.update(root)
    return document


def _errors(
    raw: Any, *, profile: ValidationProfile = ValidationProfile.RUNNABLE, registry: Any = None
):
    return validate_workflow_v3(raw, profile=profile, registry=registry)


def _codes(raw: Any, **kwargs: Any) -> list[str]:
    return [diagnostic.code for diagnostic in _errors(raw, **kwargs)]


def _definition(raw: Any):
    return parse_v3_document(raw)


def _fp(raw: Any, *, registry: Any = None) -> str:
    return workflow_definition_fingerprint_v3(_definition(raw), registry=registry)


CONF = {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}}


# ---------------------------------------------------------------------------
# G1–G13 — authoritative graph
# ---------------------------------------------------------------------------
def test_g1_valid_linear() -> None:
    raw = _doc(
        [CONF, {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}}]
    )
    assert _errors(raw) == []
    graph, diagnostics = build_validated_graph(_definition(raw).steps, require_ids=True)
    assert diagnostics == []
    assert graph is not None
    assert graph.topological_order == ("s001", "s002")
    assert graph.roots == ("s001",)
    assert graph.terminals == ("s002",)


def test_g2_valid_branch() -> None:
    raw = _doc(
        [
            CONF,
            {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
            {"id": "s003", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
        ]
    )
    assert _errors(raw) == []
    graph, _ = build_validated_graph(_definition(raw).steps, require_ids=True)
    assert graph is not None
    assert graph.terminals == ("s002", "s003")
    assert graph.ancestors("s002") == ("s001",)


def test_g3_valid_confgen_fan_in() -> None:
    raw = _doc(
        [
            {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {"id": "s002", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {
                "id": "s003",
                "type": "confgen",
                "inputs": ["s001", "s002"],
                "params": {"chains": ["1-2"]},
            },
        ]
    )
    assert _errors(raw) == []


def test_g4_duplicate_id() -> None:
    raw = _doc([CONF, {"id": "s001", "type": "calc", "inputs": [], "params": {"keyword": "HF"}}])
    assert "workflow.v3.duplicate_id" in _codes(raw)


def test_g5_unknown_predecessor() -> None:
    raw = _doc([{"id": "s001", "type": "calc", "inputs": ["nope"], "params": {"keyword": "HF"}}])
    assert "workflow.v3.unknown_input" in _codes(raw)


def test_g6_duplicate_predecessor() -> None:
    raw = _doc(
        [
            CONF,
            {
                "id": "s002",
                "type": "confgen",
                "inputs": ["s001", "s001"],
                "params": {"chains": ["1-2"]},
            },
        ]
    )
    assert "workflow.v3.duplicate_input" in _codes(raw)


def test_g7_self_reference() -> None:
    raw = _doc([{"id": "s001", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}}])
    assert "workflow.v3.self_loop" in _codes(raw)


def test_g8_two_node_cycle() -> None:
    raw = _doc(
        [
            {"id": "s001", "type": "calc", "inputs": ["s002"], "params": {"keyword": "HF"}},
            {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
        ]
    )
    assert "workflow.v3.dependency_cycle" in _codes(raw)


def test_g9_longer_cycle() -> None:
    raw = _doc(
        [
            {"id": "s001", "type": "calc", "inputs": ["s003"], "params": {"keyword": "HF"}},
            {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
            {"id": "s003", "type": "calc", "inputs": ["s002"], "params": {"keyword": "HF"}},
        ]
    )
    assert "workflow.v3.dependency_cycle" in _codes(raw)


def test_g10_multiple_roots() -> None:
    raw = _doc(
        [
            {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {"id": "s002", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
        ]
    )
    graph, _ = build_validated_graph(_definition(raw).steps, require_ids=True)
    assert graph is not None
    assert graph.roots == ("s001", "s002")


def test_g11_topological_wave_is_sorted_by_id_not_document_order() -> None:
    # Declared s009, s003 (roots), then s005 depending on both.
    raw = _doc(
        [
            {"id": "s009", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {"id": "s003", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {
                "id": "s005",
                "type": "confgen",
                "inputs": ["s009", "s003"],
                "params": {"chains": ["1-2"]},
            },
        ]
    )
    graph, _ = build_validated_graph(_definition(raw).steps, require_ids=True)
    assert graph is not None
    assert graph.topological_order == ("s003", "s009", "s005")


def test_g12_document_reorder_keeps_same_graph() -> None:
    forward = _doc(
        [CONF, {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}}]
    )
    reversed_doc = _doc(
        [{"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}}, CONF]
    )
    first, _ = build_validated_graph(_definition(forward).steps, require_ids=True)
    second, _ = build_validated_graph(_definition(reversed_doc).steps, require_ids=True)
    assert first is not None and second is not None
    assert first.predecessors == second.predecessors
    assert first.topological_order == second.topological_order


def test_g13_cycle_rejected_and_no_authoritative_graph() -> None:
    raw = _doc(
        [
            {"id": "s001", "type": "calc", "inputs": ["s002"], "params": {"keyword": "HF"}},
            {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
        ]
    )
    # The structural parser tolerates the cycle (fallback ordering) ...
    definition = _definition(raw)
    assert definition.execution_order == ("s001", "s002")  # fallback, NOT authoritative
    # ... but the semantic validator rejects it and there is no authoritative graph.
    assert "workflow.v3.dependency_cycle" in _codes(raw)
    graph, diagnostics = build_validated_graph(definition.steps, require_ids=True)
    assert graph is None
    assert [d.code for d in diagnostics] == ["workflow.v3.dependency_cycle"]


# ---------------------------------------------------------------------------
# DG1–DG6 — disabled steps and the graph
# ---------------------------------------------------------------------------
def test_dg1_disabled_unknown_predecessor_is_an_error() -> None:
    raw = _doc([{"id": "s001", "type": "calc", "enabled": False, "inputs": ["nope"], "params": {}}])
    assert "workflow.v3.unknown_input" in _codes(raw)


def test_dg2_disabled_self_reference_is_an_error() -> None:
    raw = _doc([{"id": "s001", "type": "calc", "enabled": False, "inputs": ["s001"], "params": {}}])
    assert "workflow.v3.self_loop" in _codes(raw)


def test_dg3_disabled_cycle_participant_is_an_error() -> None:
    raw = _doc(
        [
            {"id": "s001", "type": "calc", "enabled": False, "inputs": ["s002"], "params": {}},
            {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
        ]
    )
    assert "workflow.v3.dependency_cycle" in _codes(raw)


def test_dg4_disabled_calc_fan_in_is_exempt() -> None:
    raw = _doc(
        [
            {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {"id": "s002", "type": "confgen", "inputs": ["s001"], "params": {"chains": ["1-2"]}},
            {
                "id": "s003",
                "type": "calc",
                "enabled": False,
                "inputs": ["s001", "s002"],
                "params": {},
            },
        ]
    )
    assert "workflow.calc_fan_in" not in _codes(raw)


def test_dg5_enabled_calc_fan_in_is_an_error() -> None:
    raw = _doc(
        [
            {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {"id": "s002", "type": "confgen", "inputs": ["s001"], "params": {"chains": ["1-2"]}},
            {"id": "s003", "type": "calc", "inputs": ["s001", "s002"], "params": {"keyword": "HF"}},
        ]
    )
    assert "workflow.calc_fan_in" in _codes(raw)


def test_dg6_effective_bypass_cardinality_is_not_implemented_here() -> None:
    # A disabled fan-in bypass (A,D -> B(off) -> C) must NOT be cardinality-checked
    # in R3.4: effective-dataflow cardinality is R6.
    raw = _doc(
        [
            {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {"id": "s002", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {
                "id": "s003",
                "type": "confgen",
                "enabled": False,
                "inputs": ["s001", "s002"],
                "params": {},
            },
            {"id": "s004", "type": "calc", "inputs": ["s003"], "params": {"keyword": "HF"}},
        ]
    )
    assert _errors(raw) == []


# ---------------------------------------------------------------------------
# D1–D7 — disabled calc keyword semantics
# ---------------------------------------------------------------------------
def test_d1_enabled_calc_without_keyword_is_an_error() -> None:
    raw = _doc([{"id": "s001", "type": "calc", "inputs": [], "params": {"itask": "opt"}}])
    assert "workflow.v3.params_invalid" in _codes(raw)


def test_d2_disabled_calc_without_keyword_is_runnable() -> None:
    raw = _doc(
        [{"id": "s001", "type": "calc", "enabled": False, "inputs": [], "params": {"itask": "opt"}}]
    )
    assert _errors(raw) == []


def test_d3_disabled_calc_without_keyword_is_fingerprintable() -> None:
    raw = _doc(
        [{"id": "s001", "type": "calc", "enabled": False, "inputs": [], "params": {"itask": "opt"}}]
    )
    assert _fp(raw).startswith("sha256:")


def test_d4_absent_keyword_is_omitted_not_synthesised() -> None:
    raw = _doc(
        [{"id": "s001", "type": "calc", "enabled": False, "inputs": [], "params": {"itask": "opt"}}]
    )
    payload = build_workflow_definition_payload_v3(_definition(raw))
    params = payload["steps"][0]["params"]
    assert "keyword" not in params
    assert params["itask"] == "opt"
    assert all(value is not None for value in params.values())


def test_d5_explicit_valid_keyword_is_canonicalised_and_included() -> None:
    raw = _doc(
        [
            {
                "id": "s001",
                "type": "calc",
                "enabled": False,
                "inputs": [],
                "params": {"itask": "opt_freq", "noH": "true", "keyword": "HF"},
            }
        ]
    )
    assert _errors(raw) == []
    params = build_workflow_definition_payload_v3(_definition(raw))["steps"][0]["params"]
    assert params["keyword"] == "HF"
    assert params["itask"] == "opt_freq"
    assert params["noH"] is True  # string form canonicalised


def test_d6_disabled_keyword_presence_changes_the_fingerprint() -> None:
    without = _doc(
        [{"id": "s001", "type": "calc", "enabled": False, "inputs": [], "params": {"itask": "opt"}}]
    )
    with_kw = _doc(
        [
            {
                "id": "s001",
                "type": "calc",
                "enabled": False,
                "inputs": [],
                "params": {"itask": "opt", "keyword": "HF"},
            }
        ]
    )
    assert _fp(without) != _fp(with_kw)


def test_d7_supplied_invalid_value_is_not_hidden_by_disabled() -> None:
    raw = _doc(
        [
            {
                "id": "s001",
                "type": "calc",
                "enabled": False,
                "inputs": [],
                "params": {"total_memory": "zap"},
            }
        ]
    )
    assert "workflow.v3.params_invalid" in _codes(raw)


# ---------------------------------------------------------------------------
# C1–C10 — checkpoint semantics
# ---------------------------------------------------------------------------
_HF = {"keyword": "HF"}
_CP = [
    {"id": "s001", "type": "calc", "inputs": [], "params": _HF},
    {"id": "s002", "type": "calc", "inputs": ["s001"], "params": _HF},
    {"id": "s003", "type": "calc", "inputs": ["s002"], "params": _HF},
]


def test_c1_ancestor_checkpoint_is_valid() -> None:
    raw = _doc([_CP[0], _CP[1], {**_CP[2], "checkpoint": {"from_step": "s001"}}])
    assert _errors(raw) == []


def test_c2_self_checkpoint_is_an_error() -> None:
    raw = _doc([{**_CP[0], "checkpoint": {"from_step": "s001"}}])
    assert "workflow.v3.checkpoint_self" in _codes(raw)


def test_c3_sibling_checkpoint_is_an_error() -> None:
    raw = _doc(
        [
            {"id": "s001", "type": "calc", "inputs": [], "params": _HF},
            {"id": "s002", "type": "calc", "inputs": ["s001"], "params": _HF},
            {
                "id": "s003",
                "type": "calc",
                "inputs": ["s001"],
                "params": _HF,
                "checkpoint": {"from_step": "s002"},
            },
        ]
    )
    assert "workflow.v3.checkpoint_not_ancestor" in _codes(raw)


def test_c4_descendant_checkpoint_is_an_error() -> None:
    raw = _doc(
        [
            {"id": "s001", "type": "calc", "inputs": [], "params": _HF},
            {"id": "s002", "type": "calc", "inputs": ["s001"], "params": _HF},
            {"id": "s003", "type": "calc", "inputs": ["s002"], "params": _HF},
            {
                "id": "s001b",
                "type": "calc",
                "inputs": ["s003"],
                "params": _HF,
                "checkpoint": {"from_step": "s003"},
            },
        ]
    )
    # s003 IS an ancestor of s001b, so that is fine; make it a real descendant case:
    descendant = _doc(
        [
            {"id": "s001", "type": "calc", "inputs": [], "params": _HF},
            {
                "id": "s002",
                "type": "calc",
                "inputs": ["s001"],
                "params": _HF,
                "checkpoint": {"from_step": "s003"},
            },
            {"id": "s003", "type": "calc", "inputs": ["s002"], "params": _HF},
        ]
    )
    assert _errors(raw) == []
    assert "workflow.v3.checkpoint_not_ancestor" in _codes(descendant)


def test_c5_unrelated_root_checkpoint_is_an_error() -> None:
    raw = _doc(
        [
            {"id": "s001", "type": "calc", "inputs": [], "params": _HF},
            {
                "id": "s002",
                "type": "calc",
                "inputs": [],
                "params": _HF,
                "checkpoint": {"from_step": "s001"},
            },
        ]
    )
    assert "workflow.v3.checkpoint_not_ancestor" in _codes(raw)


def test_c6_unknown_checkpoint_target_is_an_error() -> None:
    raw = _doc([{**_CP[0], "checkpoint": {"from_step": "s999"}}])
    assert "workflow.v3.checkpoint_unknown" in _codes(raw)


def test_c7_enabled_confgen_checkpoint_is_an_error() -> None:
    raw = _doc([{**CONF, "checkpoint": {"from_step": "s001"}}])
    assert "workflow.v3.checkpoint_not_calc" in _codes(raw)


def test_c8_disabled_confgen_checkpoint_is_an_error() -> None:
    raw = _doc([{**CONF, "enabled": False, "checkpoint": {"from_step": "s001"}}])
    assert "workflow.v3.checkpoint_not_calc" in _codes(raw)


def test_c9_disabled_calc_checkpoint_resolution_is_exempt() -> None:
    raw = _doc(
        [
            {"id": "s001", "type": "calc", "inputs": [], "params": _HF},
            {
                "id": "s002",
                "type": "calc",
                "enabled": False,
                "inputs": [],
                "params": {},
                "checkpoint": {"from_step": "s999"},
            },
        ]
    )
    assert _errors(raw) == []


def test_c10_checkpoint_does_not_add_a_graph_edge() -> None:
    raw = _doc(
        [
            {"id": "s001", "type": "calc", "inputs": [], "params": _HF},
            {"id": "s002", "type": "calc", "inputs": ["s001"], "params": _HF},
            {
                "id": "s003",
                "type": "calc",
                "inputs": ["s002"],
                "params": _HF,
                "checkpoint": {"from_step": "s001"},
            },
        ]
    )
    graph, _ = build_validated_graph(_definition(raw).steps, require_ids=True)
    assert graph is not None
    # s003's declared inputs are unchanged by its checkpoint reference.
    assert graph.predecessors["s003"] == ("s002",)


def test_c11_confgen_ancestor_target_is_a_definition_error() -> None:
    # RFC §11 (frozen): "from_step targets a calc step" is a definition-layer
    # rule, not an artifact-capability inference. A confgen strict ancestor is
    # still rejected at the definition layer; whether the target produces a
    # consumable checkpoint artifact stays R4/R6.
    raw = _doc(
        [
            CONF,
            {
                "id": "s002",
                "type": "calc",
                "inputs": ["s001"],
                "params": _HF,
                "checkpoint": {"from_step": "s001"},
            },
        ]
    )
    assert "workflow.v3.checkpoint_target_not_calc" in _codes(raw)
    # The declaring-step rule is unaffected by the §12 exemption: a disabled
    # confgen declaring a checkpoint is still checkpoint_not_calc (c8).
    disabled_current = _doc(
        [
            {"id": "s001", "type": "calc", "inputs": [], "params": _HF},
            {
                "id": "s002",
                "type": "confgen",
                "enabled": False,
                "inputs": ["s001"],
                "params": {},
                "checkpoint": {"from_step": "s001"},
            },
        ]
    )
    assert "workflow.v3.checkpoint_not_calc" in _codes(disabled_current)


# ---------------------------------------------------------------------------
# E1–E7 — extension recognition
# ---------------------------------------------------------------------------
def _registry() -> ExtensionRegistry:
    registry = ExtensionRegistry()
    registry.register("vendor.example", schema={"type": "object", "required": ["tier"]})
    return registry


def test_e1_unknown_extension_is_a_runnable_error() -> None:
    raw = _doc([{**CONF, "extensions": {"vendor.unknown": {"x": 1}}}])
    assert "workflow.v3.extension_unknown" in _codes(raw)


def test_e2_disabled_step_unknown_extension_is_still_an_error() -> None:
    raw = _doc([{**CONF, "enabled": False, "extensions": {"vendor.unknown": {"x": 1}}}])
    assert "workflow.v3.extension_unknown" in _codes(raw)


def test_e3_fragment_preserves_an_unknown_extension() -> None:
    raw = _doc(
        [
            {
                "type": "confgen",
                "inputs": [],
                "params": {},
                "extensions": {"vendor.unknown": {"x": 1}},
            }
        ]
    )
    assert _errors(raw, profile=ValidationProfile.FRAGMENT) == []


def test_e4_registered_extension_passes() -> None:
    raw = _doc([{**CONF, "extensions": {"vendor.example": {"tier": 2}}}])
    assert _errors(raw, registry=_registry()) == []


def test_e5_invalid_registered_payload_is_an_error() -> None:
    raw = _doc([{**CONF, "extensions": {"vendor.example": {"missing_tier": True}}}])
    assert "workflow.v3.extension_payload" in _codes(raw, registry=_registry())


def test_e6_extension_change_changes_the_fingerprint() -> None:
    registry = _registry()
    first = _doc([{**CONF, "extensions": {"vendor.example": {"tier": 1}}}])
    second = _doc([{**CONF, "extensions": {"vendor.example": {"tier": 2}}}])
    assert _fp(first, registry=registry) != _fp(second, registry=registry)


def test_e7_extension_mapping_key_order_is_irrelevant() -> None:
    registry = _registry()
    first = _doc([{**CONF, "extensions": {"vendor.example": {"tier": 1, "mode": "fast"}}}])
    second = _doc([{**CONF, "extensions": {"vendor.example": {"mode": "fast", "tier": 1}}}])
    assert _fp(first, registry=registry) == _fp(second, registry=registry)


# ---------------------------------------------------------------------------
# P1–P7 — semantic params / profiles
# ---------------------------------------------------------------------------
def test_p1_p3_valid_calc_and_confgen_resolve() -> None:
    raw = _doc(
        [
            CONF,
            {
                "id": "s002",
                "type": "calc",
                "inputs": ["s001"],
                "params": {"keyword": "HF", "iprog": "g16"},
            },
        ]
    )
    assert _errors(raw) == []


def test_p2_invalid_calc_value_is_rejected() -> None:
    # An enum-invalid value is caught structurally; a schema-valid but
    # semantically-invalid value is caught by the semantic layer.
    structural = _doc(
        [
            {
                "id": "s001",
                "type": "calc",
                "inputs": [],
                "params": {"keyword": "HF", "itask": "bogus"},
            }
        ]
    )
    assert "workflow.v3.schema" in _codes(structural)
    semantic = _doc(
        [
            {
                "id": "s001",
                "type": "calc",
                "inputs": [],
                "params": {"keyword": "HF", "total_memory": "zap"},
            }
        ]
    )
    assert "workflow.v3.params_invalid" in _codes(semantic)


def test_p4_confgen_missing_chains_is_rejected_when_enabled() -> None:
    raw = _doc([{"id": "s001", "type": "confgen", "inputs": [], "params": {}}])
    assert "confgen.chains.required" in _codes(raw)


def test_p5_confgen_angle_step_non_positive_is_rejected() -> None:
    raw = _doc(
        [
            {
                "id": "s001",
                "type": "confgen",
                "inputs": [],
                "params": {"chains": ["1-2"], "angle_step": -10},
            }
        ]
    )
    assert "confgen.angle_step.invalid" in _codes(raw)


def test_p6_fragment_allows_incomplete_required_field() -> None:
    raw = _doc([{"label": "starter", "type": "confgen", "inputs": [], "params": {}}])
    assert _errors(raw, profile=ValidationProfile.FRAGMENT) == []


def test_p7_fragment_supplied_invalid_value_is_still_rejected() -> None:
    raw = _doc(
        [{"type": "confgen", "inputs": [], "params": {"chains": ["1-2"], "angle_step": -10}}]
    )
    assert "confgen.angle_step.invalid" in _codes(raw, profile=ValidationProfile.FRAGMENT)


# ---------------------------------------------------------------------------
# Fragment handles / RUNNABLE id isolation
# ---------------------------------------------------------------------------
def test_fragment_missing_id_is_accepted_but_runnable_is_not() -> None:
    raw = _doc(
        [{"label": "starter", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}}]
    )
    assert _errors(raw, profile=ValidationProfile.FRAGMENT) == []
    # Redundant with the DOCUMENT schema, but the semantic layer enforces it too
    # (a directly-built IR can bypass the schema):
    assert _codes(raw) != []
    step = CanonicalStepDefinition(
        name="#step1",
        type="calc",
        enabled=True,
        params={"keyword": "HF"},
        predecessors=(),
        inputs_declared=True,
        v2_name="#step1",
        id=None,
    )
    definition = CanonicalWorkflowDefinition(
        global_config={},
        global_options=WorkflowConfig.from_mapping({"global": {}}).global_options,
        steps=(step,),
        dependency_mode="explicit",
        predecessors={"#step1": ()},
        execution_order=("#step1",),
        terminal_steps=("#step1",),
        source_version=V3,
    )
    assert [d.code for d in validate_v3_definition(definition)] == ["workflow.v3.missing_id"]


def test_fragment_handle_cannot_be_a_runnable_identity() -> None:
    step = CanonicalStepDefinition(
        name="#step1",
        type="calc",
        enabled=True,
        params={"keyword": "HF"},
        predecessors=(),
        inputs_declared=True,
        v2_name="#step1",
        id="#step1",
    )
    definition = CanonicalWorkflowDefinition(
        global_config={},
        global_options=WorkflowConfig.from_mapping({"global": {}}).global_options,
        steps=(step,),
        dependency_mode="explicit",
        predecessors={"#step1": ()},
        execution_order=("#step1",),
        terminal_steps=("#step1",),
        source_version=V3,
    )
    diagnostics = validate_v3_definition(definition, profile=ValidationProfile.RUNNABLE)
    assert [d.code for d in diagnostics] == ["workflow.v3.invalid_id"]


def test_fingerprint_rejects_a_fragment() -> None:
    raw = _doc([{"label": "starter", "type": "confgen", "inputs": [], "params": {}}])
    fragment = parse_v3_document(raw, profile=SchemaProfile.FRAGMENT)
    with pytest.raises(WorkflowFingerprintError):
        workflow_definition_fingerprint_v3(fragment)


# ---------------------------------------------------------------------------
# FP1–FP18 — fingerprint properties
# ---------------------------------------------------------------------------
def _fp_doc(steps: list[dict[str, Any]], **root: Any) -> str:
    return _fp(_doc(steps, **root))


BASE = [
    {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
    {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
]


def test_fp1_step_array_reorder_same() -> None:
    assert _fp_doc(BASE) == _fp_doc(list(reversed(BASE)))


def test_fp2_label_rename_same() -> None:
    assert _fp_doc(BASE) == _fp_doc([{**BASE[0], "label": "gen"}, {**BASE[1], "label": "sp"}])


def test_fp3_annotation_change_same() -> None:
    assert _fp_doc(BASE) == _fp_doc([{**BASE[0], "annotations": {"ui": 1}}, BASE[1]])


def test_fp4_migration_annotation_change_same() -> None:
    with_migration = [
        {**BASE[0], "annotations": {"confflow.migration.v2": {"unknown_fields": {"x": 1}}}},
        BASE[1],
    ]
    assert _fp_doc(BASE) == _fp_doc(with_migration)


def test_fp5_stable_id_change_differs() -> None:
    changed = [
        {"id": "s010", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
        {"id": "s011", "type": "calc", "inputs": ["s010"], "params": {"keyword": "HF"}},
    ]
    assert _fp_doc(BASE) != _fp_doc(changed)


def test_fp6_graph_edge_change_differs() -> None:
    changed = [BASE[0], {**BASE[1], "inputs": []}]
    assert _fp_doc(BASE) != _fp_doc(changed)


def test_fp7_enabled_change_differs() -> None:
    changed = [BASE[0], {**BASE[1], "enabled": False}]
    assert _fp_doc(BASE) != _fp_doc(changed)


def test_fp8_semantic_param_change_differs() -> None:
    changed = [BASE[0], {**BASE[1], "params": {"keyword": "B3LYP/6-31G*"}}]
    assert _fp_doc(BASE) != _fp_doc(changed)


def test_fp9_extension_change_differs() -> None:
    registry = _registry()
    with_ext = _doc([{**CONF, "extensions": {"vendor.example": {"tier": 1}}}])
    changed = _doc([{**CONF, "extensions": {"vendor.example": {"tier": 2}}}])
    assert _fp(with_ext, registry=registry) != _fp(changed, registry=registry)


def test_fp10_extension_key_order_same() -> None:
    registry = _registry()
    first = _doc([{**CONF, "extensions": {"vendor.example": {"tier": 1, "mode": "x"}}}])
    second = _doc([{**CONF, "extensions": {"vendor.example": {"mode": "x", "tier": 1}}}])
    assert _fp(first, registry=registry) == _fp(second, registry=registry)


def test_fp11_yaml_map_ordering_same() -> None:
    first = _doc([BASE[0], {**BASE[1], "params": {"keyword": "HF", "iprog": "g16"}}])
    second = _doc([BASE[0], {**BASE[1], "params": {"iprog": "g16", "keyword": "HF"}}])
    assert _fp(first) == _fp(second)


def test_fp12_schema_metadata_is_excluded() -> None:
    payload = build_workflow_definition_payload_v3(_definition(_doc(BASE)))
    for forbidden in (
        "schema",
        "workflow_schema",
        "workflow_schema_sha256",
        "source_version",
        "producer",
    ):
        assert forbidden not in payload
    assert payload["semantics_version"] == "confflow.workflow-semantics.v3"


def test_fp13_inputs_permutation_same() -> None:
    first = _doc(
        [
            {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {"id": "s002", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {
                "id": "s003",
                "type": "confgen",
                "inputs": ["s001", "s002"],
                "params": {"chains": ["1-2"]},
            },
        ]
    )
    second = _doc(
        [
            {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {"id": "s002", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {
                "id": "s003",
                "type": "confgen",
                "inputs": ["s002", "s001"],
                "params": {"chains": ["1-2"]},
            },
        ]
    )
    assert _fp(first) == _fp(second)


def test_fp14_explicit_semantic_default_equals_omitted() -> None:
    omitted = _doc([{"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}}])
    explicit = _doc(
        [
            {
                "id": "s001",
                "type": "confgen",
                "inputs": [],
                "params": {"chains": ["1-2"], "angle_step": 120},
            }
        ]
    )
    assert _fp(omitted) == _fp(explicit)


def test_fp15_calc_alias_equals_canonical() -> None:
    alias = _doc(
        [
            {
                "id": "s001",
                "type": "calc",
                "inputs": [],
                "params": {"keyword": "HF", "clean_opts": "--dedup-only"},
            }
        ]
    )
    canonical = _doc(
        [
            {
                "id": "s001",
                "type": "calc",
                "inputs": [],
                "params": {"keyword": "HF", "clean_params": "--dedup-only"},
            }
        ]
    )
    assert _fp(alias) == _fp(canonical)


def test_fp16_confgen_alias_equals_canonical() -> None:
    alias = _doc([{"id": "s001", "type": "confgen", "inputs": [], "params": {"chain": ["1-2"]}}])
    canonical = _doc(
        [{"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}}]
    )
    assert _fp(alias) == _fp(canonical)


def test_fp17_disabled_exempt_field_omission_is_deterministic() -> None:
    raw = _doc(
        [{"id": "s001", "type": "calc", "enabled": False, "inputs": [], "params": {"itask": "opt"}}]
    )
    assert _fp(raw) == _fp(raw)


def test_fp18_disabled_explicit_exempt_value_differs_from_absent() -> None:
    absent = _doc(
        [{"id": "s001", "type": "calc", "enabled": False, "inputs": [], "params": {"itask": "opt"}}]
    )
    present = _doc(
        [
            {
                "id": "s001",
                "type": "calc",
                "enabled": False,
                "inputs": [],
                "params": {"itask": "opt", "keyword": "HF"},
            }
        ]
    )
    assert _fp(absent) != _fp(present)


def test_fp_execution_class_changes_do_not_move_the_definition_fingerprint() -> None:
    first = _fp(_doc(BASE, **{"global": {"max_parallel_jobs": 1}}))
    second = _fp(_doc(BASE, **{"global": {"max_parallel_jobs": 8}}))
    assert first == second


# ---------------------------------------------------------------------------
# X1–X4 — frozen per-step execution-class exclusions (RFC §16.A)
# ---------------------------------------------------------------------------
# The authoritative constant itself is the parametrization source: the test
# follows the frozen set, and any member added without a case here fails loudly.
# Each case is (step index, value): the V3 schema is a closed vocabulary, so
# `workers` is settable on the confgen step only, the resource/runtime names on
# the calc step (where they override the global default).
_X1_CASES: dict[str, tuple[int, Any]] = {
    "gaussian_path": (1, "/opt/g16/custom"),
    "orca_path": (1, "/opt/orca/custom"),
    "cores_per_task": (1, 4),
    "total_memory": (1, "16GB"),
    "max_parallel_jobs": (1, 3),
    "orca_maxcore": (1, 8000),
    "enable_dynamic_resources": (1, True),
    "delete_work_dir": (1, False),
    "stop_check_interval_seconds": (1, 9.0),
    "sandbox_root": (1, "/tmp/sandbox"),
    "input_chk_dir": (1, "/tmp/chk"),
    "allowed_executables": (1, ["g16"]),
    "gaussian_write_chk": (1, True),
    "max_wall_time_seconds": (1, 3600.0),
    "resume_from_backups": (1, True),
    "workers": (0, 2),
}


@pytest.mark.parametrize("name", sorted(_EXECUTION_CLASS_STEP_PARAMS))
def test_x1_execution_class_step_param_never_moves_the_definition_fingerprint(name: str) -> None:
    assert name in _X1_CASES, f"no X1 case pinned for execution-class param {name!r}"
    index, value = _X1_CASES[name]
    baseline = _fp(_doc(BASE))
    variant = copy.deepcopy(BASE)
    variant[index]["params"][name] = value
    assert _fp(_doc(variant)) == baseline


@pytest.mark.parametrize("alias", ["workers", "max_workers", "max_parallel_jobs"])
def test_x1b_confgen_workers_alias_never_moves_the_definition_fingerprint(alias: str) -> None:
    baseline = _fp(_doc(BASE))
    variant = copy.deepcopy(BASE)
    variant[0]["params"][alias] = 5
    assert _fp(_doc(variant)) == baseline


def test_x2_representative_scientific_param_change_moves_the_definition_fingerprint() -> None:
    baseline = _fp(_doc(BASE))
    keyword_changed = copy.deepcopy(BASE)
    keyword_changed[1]["params"]["keyword"] = "B3LYP/6-31G(d)"
    assert _fp(_doc(keyword_changed)) != baseline
    chains_changed = copy.deepcopy(BASE)
    chains_changed[0]["params"]["chains"] = ["3-4"]
    assert _fp(_doc(chains_changed)) != baseline
    angle_changed = copy.deepcopy(BASE)
    angle_changed[0]["params"]["angle_step"] = 60
    assert _fp(_doc(angle_changed)) != baseline


def test_x3_execution_class_default_equals_omitted() -> None:
    # Omitting an execution-class step param is fingerprint-identical to
    # supplying the resolved default value (RFC §16.A frozen rule).
    omitted = _fp(_doc(BASE, **{"global": {"max_parallel_jobs": 4, "cores_per_task": 2}}))
    explicit = _doc(BASE, **{"global": {"max_parallel_jobs": 4, "cores_per_task": 2}})
    explicit["steps"][0]["params"]["workers"] = 4
    explicit["steps"][1]["params"]["cores_per_task"] = 2
    assert _fp(explicit) == omitted


def test_x4_v2_workflow_fingerprint_is_independent_of_the_v3_exclusion_set(
    tmp_path: Any,
) -> None:
    # The exclusion set is a V3-definition-fingerprint concept only: the V2
    # algorithm never reads it and keeps execution-class settings inside its
    # digest by design (changing them moves the V2 fingerprint).
    import yaml

    from confflow.config.canonical import workflow_fingerprint
    from confflow.workflow.plan import build_workflow_plan

    base = {
        "global": {"iprog": "orca", "itask": "sp", "total_memory": "4GB"},
        "steps": [{"name": "calc", "type": "calc", "params": {"keyword": "HF"}}],
    }
    fingerprints = []
    for cores in ("1", "4"):
        root = tmp_path / cores
        root.mkdir(parents=True)
        (root / "input.xyz").write_text("1\nseed\nH 0 0 0\n", encoding="utf-8")
        config = dict(base)
        config["global"] = {**base["global"], "cores_per_task": cores}
        (root / "workflow.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        plan = build_workflow_plan([str(root / "input.xyz")], str(root / "workflow.yaml"))
        fingerprints.append(workflow_fingerprint(plan))
    assert fingerprints[0] != fingerprints[1]


# ---------------------------------------------------------------------------
# Invalid fingerprints
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    [
        _doc(
            [
                {"id": "s001", "type": "calc", "inputs": ["s002"], "params": {"keyword": "HF"}},
                {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
            ]
        ),
        _doc([{"id": "s001", "type": "calc", "inputs": ["nope"], "params": {"keyword": "HF"}}]),
        _doc([CONF, {**CONF}]),
        _doc([{"label": "x", "type": "confgen", "inputs": [], "params": {}}]),
        _doc([{**CONF, "extensions": {"vendor.unknown": {"x": 1}}}]),
        _doc(
            [
                {"id": "s001", "type": "calc", "inputs": [], "params": {"keyword": "HF"}},
                {
                    "id": "s002",
                    "type": "calc",
                    "inputs": [],
                    "params": {"keyword": "HF"},
                    "checkpoint": {"from_step": "s001"},
                },
            ]
        ),
        _doc([{"id": "s001", "type": "calc", "inputs": [], "params": {"itask": "opt"}}]),
        _doc(
            [
                {
                    "id": "s001",
                    "type": "calc",
                    "inputs": [],
                    "params": {"keyword": "HF", "itask": "bogus"},
                }
            ]
        ),
    ],
)
def test_invalid_definitions_cannot_be_fingerprinted(raw: dict[str, Any]) -> None:
    # Both layers refuse to produce a fingerprint: schema-invalid documents never
    # become a definition, and semantically-invalid ones raise at the fingerprint.
    with pytest.raises((WorkflowFingerprintError, ConfigValidationError)):
        _fp(raw)


# ---------------------------------------------------------------------------
# RUNNABLE-valid => fingerprintable invariant
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    [
        _doc(BASE),
        _doc(
            [
                {
                    "id": "s001",
                    "type": "calc",
                    "enabled": False,
                    "inputs": [],
                    "params": {"itask": "opt"},
                }
            ]
        ),
        _doc([CONF]),
        _doc([{"id": "s001", "type": "confgen", "enabled": False, "inputs": [], "params": {}}]),
        _doc([_CP[0], {**_CP[1], "checkpoint": {"from_step": "s001"}}]),
    ],
)
def test_runnable_valid_definitions_are_always_fingerprintable(raw: dict[str, Any]) -> None:
    assert _errors(raw) == []
    assert _fp(raw).startswith("sha256:")


# ---------------------------------------------------------------------------
# Deterministic diagnostics
# ---------------------------------------------------------------------------
def test_diagnostics_are_deterministic_for_invalid_definitions() -> None:
    raw = _doc(
        [
            {"id": "s001", "type": "confgen", "inputs": ["nope"], "params": {}},
            {"id": "s002", "type": "confgen", "inputs": ["s001"], "params": {}},
        ]
    )
    first = [(d.code, d.path, d.step_ref) for d in _errors(raw)]
    second = [(d.code, d.path, d.step_ref) for d in _errors(raw)]
    assert first == second
    assert first  # non-empty


def test_duplicate_labels_are_allowed() -> None:
    raw = _doc([{**CONF, "label": "Same"}, {**BASE[1], "label": "Same"}])
    assert _errors(raw) == []


# ---------------------------------------------------------------------------
# Golden fingerprints (frozen semantic digests)
# ---------------------------------------------------------------------------
GOLDEN = {
    "linear": (
        _doc(BASE),
        "sha256:f74fd4f280ff90b7c2e833bd228d62f0dd60d900f3f2affdfa645facec98c400",
    ),
    "branch": (
        _doc(
            [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
                {"id": "s003", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
            ]
        ),
        "sha256:7458c017251f5a69564e08c21f90238247e8a1fc83ec2501b438212f5ee939e2",
    ),
    "checkpoint": (
        _doc(
            [
                {"id": "s001", "type": "calc", "inputs": [], "params": {"keyword": "HF"}},
                {
                    "id": "s002",
                    "type": "calc",
                    "inputs": ["s001"],
                    "params": {"keyword": "HF"},
                    "checkpoint": {"from_step": "s001"},
                },
            ]
        ),
        "sha256:3dde3da38d49c2669df69a480943f76b0ca70ced9821247bed7b408417af494e",
    ),
    "disabled_missing_keyword": (
        _doc(
            [
                {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                {
                    "id": "s002",
                    "type": "calc",
                    "enabled": False,
                    "inputs": ["s001"],
                    "params": {"itask": "opt"},
                },
            ]
        ),
        "sha256:0eb69ed1661fedb612e78c8458b9d5e7724130e3f57e5f0bdfb1568f5ea796a8",
    ),
}


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_golden_definition_fingerprint(name: str) -> None:
    raw, expected = GOLDEN[name]
    assert _fp(raw) == expected


def test_golden_registered_extension_fingerprint() -> None:
    registry = _registry()
    raw = _doc([{**CONF, "extensions": {"vendor.example": {"tier": 2}}}])
    assert _fp(raw, registry=registry) == (
        "sha256:0947f930d87e7cef882a535206a24eec3e9362f36ab835176987748de409ea1b"
    )


# ---------------------------------------------------------------------------
# Order key
# ---------------------------------------------------------------------------
def test_v3_id_order_key_is_numeric_for_sequential_ids() -> None:
    assert sorted(["s010", "s002", "s1000"], key=v3_id_order_key) == ["s002", "s010", "s1000"]
    assert v3_id_order_key("s002") < v3_id_order_key("s010")
    assert v3_id_order_key("s002") < v3_id_order_key("s_configured")


def test_payload_is_not_mutated_by_fingerprinting() -> None:
    raw = _doc(BASE)
    before = copy.deepcopy(raw)
    _fp(raw)
    assert raw == before
