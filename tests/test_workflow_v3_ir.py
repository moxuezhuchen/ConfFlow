#!/usr/bin/env python3

"""R3.1 — V3-ready canonical IR capacity tests.

These tests prove the canonical IR can *carry* the future Workflow V3 fields
(``id`` / ``label`` / ``checkpoint_from`` / ``extensions`` / ``annotations`` /
``source_version``) without changing any V2 behaviour. No V3 YAML parsing,
schema, version dispatch or fingerprint is added in R3.1: the V3-shaped cases
construct the value objects directly in Python (see the R3.1 plan).

The V2 compatibility tests assert the adapter keeps ``id`` unset, maps the V2
name to ``label``, leaves ``params.chk_from_step`` untouched and produces a
byte-identical V2 execution projection.
"""

from __future__ import annotations

from typing import Any

import pytest

from confflow.config.canonical import to_canonical_workflow
from confflow.config.canonical.schema import WORKFLOW_SCHEMA_VERSION
from confflow.config.canonical.types import WorkflowConfig
from confflow.config.canonical.workflow import (
    CanonicalStepDefinition,
    CanonicalWorkflowDefinition,
)
from confflow.workflow.plan import build_workflow_plan

V2_MAPPING: dict[str, Any] = {
    "global": {"iprog": "orca", "itask": "sp"},
    "tools": {"vendor": "kept-as-root-extension"},
    "steps": [
        {
            "name": "gen",
            "type": "confgen",
            "params": {"chains": ["1-2"]},
            "note": "kept-as-step-extension",
        },
        {"name": "sp", "type": "calc", "params": {"keyword": "HF"}},
    ],
}


def _v2_definition(mapping: dict[str, Any] = V2_MAPPING) -> CanonicalWorkflowDefinition:
    return to_canonical_workflow(WorkflowConfig.from_mapping(mapping))


# ---------------------------------------------------------------------------
# T1 — V2 IR compatibility (no behaviour change)
# ---------------------------------------------------------------------------
def test_v2_ir_projection_is_unchanged() -> None:
    workflow = WorkflowConfig.from_mapping(V2_MAPPING)
    definition = to_canonical_workflow(workflow)

    global_config, steps = definition.to_v2_execution_shape()

    assert global_config == workflow.as_legacy_shape()["global"]
    assert steps == workflow.as_legacy_shape()["steps"]
    assert definition.predecessors == {"gen": (), "sp": ("gen",)}
    assert definition.execution_order == ("gen", "sp")
    assert definition.terminal_steps == ("sp",)
    # extensions are carried verbatim
    assert definition.extensions == {"tools": {"vendor": "kept-as-root-extension"}}
    assert definition.steps[0].extensions == {"note": "kept-as-step-extension"}


# ---------------------------------------------------------------------------
# T2 — source_version
# ---------------------------------------------------------------------------
def test_v2_adapter_sets_the_v2_source_version() -> None:
    assert _v2_definition().source_version == WORKFLOW_SCHEMA_VERSION
    assert WORKFLOW_SCHEMA_VERSION == "confflow.workflow.v2"


# ---------------------------------------------------------------------------
# T3 — V2 name -> label projection, execution still speaks "name"
# ---------------------------------------------------------------------------
def test_v2_name_becomes_label_but_projection_keeps_name() -> None:
    definition = _v2_definition(
        {"global": {}, "steps": [{"name": "opt", "type": "calc", "params": {"keyword": "HF"}}]}
    )

    (step,) = definition.steps
    assert step.name == "opt"
    assert step.label == "opt"

    _, steps = definition.to_v2_execution_shape()
    assert steps[0]["name"] == "opt"
    assert "label" not in steps[0]


def test_whitespace_and_generated_v2_names_map_to_the_canonical_label() -> None:
    definition = _v2_definition(
        {
            "global": {},
            "steps": [
                {"name": "  padded  ", "type": "confgen", "params": {"chains": ["1-2"]}},
                {"type": "confgen", "params": {"chains": ["1-2"]}},
            ],
        }
    )

    padded, generated = definition.steps
    assert padded.name == "padded"
    assert padded.label == "padded"
    assert generated.label == generated.name == "confgen_2"


# ---------------------------------------------------------------------------
# T4 — V2 steps carry no stable id
# ---------------------------------------------------------------------------
def test_v2_adapted_steps_have_no_stable_id() -> None:
    definition = _v2_definition()

    assert all(step.id is None for step in definition.steps)
    # identity falls back to the V2 name, so nothing downstream loses an identity
    assert [step.identity for step in definition.steps] == ["gen", "sp"]


# ---------------------------------------------------------------------------
# T5 — V3-shaped IR (constructed directly; no parser)
# ---------------------------------------------------------------------------
def _v3_step(**overrides: Any) -> CanonicalStepDefinition:
    base: dict[str, Any] = {
        "id": "s_abcd1234",
        "label": "Geometry Optimization",
        "name": "s_abcd1234",
        "type": "calc",
        "enabled": True,
        "params": {"keyword": "HF"},
        "predecessors": (),
        "inputs_declared": True,
        "v2_name": "s_abcd1234",
    }
    base.update(overrides)
    return CanonicalStepDefinition(**base)


def test_v3_shaped_ir_can_be_represented() -> None:
    definition = CanonicalWorkflowDefinition(
        global_config={},
        global_options=WorkflowConfig.from_mapping({"global": {}}).global_options,
        steps=(
            _v3_step(),
            _v3_step(
                id="s_efgh5678",
                label="Single Point",
                name="s_efgh5678",
                v2_name="s_efgh5678",
                predecessors=("s_abcd1234",),
            ),
        ),
        dependency_mode="explicit",
        predecessors={"s_abcd1234": (), "s_efgh5678": ("s_abcd1234",)},
        execution_order=("s_abcd1234", "s_efgh5678"),
        terminal_steps=("s_efgh5678",),
        source_version="confflow.workflow.v3",
    )

    assert definition.source_version == "confflow.workflow.v3"
    assert [step.identity for step in definition.steps] == ["s_abcd1234", "s_efgh5678"]


# ---------------------------------------------------------------------------
# T6 — duplicate labels are representable at the model layer
# ---------------------------------------------------------------------------
def test_duplicate_labels_are_allowed_at_the_model_layer() -> None:
    definition = CanonicalWorkflowDefinition(
        global_config={},
        global_options=WorkflowConfig.from_mapping({"global": {}}).global_options,
        steps=(
            _v3_step(
                id="s_aaaa1111", label="Single Point", name="s_aaaa1111", v2_name="s_aaaa1111"
            ),
            _v3_step(
                id="s_bbbb2222", label="Single Point", name="s_bbbb2222", v2_name="s_bbbb2222"
            ),
        ),
        dependency_mode="explicit",
        predecessors={"s_aaaa1111": (), "s_bbbb2222": ()},
        execution_order=("s_aaaa1111", "s_bbbb2222"),
        terminal_steps=("s_aaaa1111", "s_bbbb2222"),
        source_version="confflow.workflow.v3",
    )

    assert [step.label for step in definition.steps] == ["Single Point", "Single Point"]
    assert [step.identity for step in definition.steps] == ["s_aaaa1111", "s_bbbb2222"]


# ---------------------------------------------------------------------------
# T7 — annotations are carried but never reach the V2 projection
# ---------------------------------------------------------------------------
def test_annotations_are_carried_and_never_projected() -> None:
    root_annotations = {"ui": {"x": 12, "y": 40}}
    step_annotations = {"colour": "blue"}
    definition = CanonicalWorkflowDefinition(
        global_config={},
        global_options=WorkflowConfig.from_mapping({"global": {}}).global_options,
        steps=(_v3_step(annotations=step_annotations),),
        dependency_mode="explicit",
        predecessors={"s_abcd1234": ()},
        execution_order=("s_abcd1234",),
        terminal_steps=("s_abcd1234",),
        source_version="confflow.workflow.v3",
        annotations=root_annotations,
    )

    assert definition.annotations == root_annotations
    assert definition.steps[0].annotations == step_annotations
    # V2 projection must not leak annotations
    _, steps = definition.to_v2_execution_shape()
    assert "annotations" not in steps[0]

    # V2 adapter also carries no annotations
    v2 = _v2_definition()
    assert v2.annotations == {}
    assert all(step.annotations == {} for step in v2.steps)


# ---------------------------------------------------------------------------
# T8 — structured checkpoint reference vs V2 params.chk_from_step
# ---------------------------------------------------------------------------
def test_structured_checkpoint_and_legacy_chk_from_step_coexist() -> None:
    # V3-shaped: structured reference lives on the step
    v3_step = _v3_step(checkpoint_from="s_abcd1234")
    assert v3_step.checkpoint_from == "s_abcd1234"

    # V2: the legacy key stays verbatim inside params; the structured field is unset
    definition = _v2_definition(
        {
            "global": {},
            "steps": [
                {"name": "first", "type": "calc", "params": {"keyword": "HF"}},
                {
                    "name": "second",
                    "type": "calc",
                    "params": {"keyword": "HF", "chk_from_step": "first"},
                },
            ],
        }
    )
    assert definition.steps[1].checkpoint_from is None
    assert definition.steps[1].params["chk_from_step"] == "first"

    _, steps = definition.to_v2_execution_shape()
    assert steps[1]["params"]["chk_from_step"] == "first"


# ---------------------------------------------------------------------------
# T9 — semantic extensions are carried losslessly
# ---------------------------------------------------------------------------
def test_extensions_are_carried_losslessly() -> None:
    payload = {"vendor.example": {"basis": "def2-TZVP", "nested": [1, {"deep": True}]}}
    step = _v3_step(extensions=payload)

    assert step.extensions == payload


# ---------------------------------------------------------------------------
# T10 — predecessor refs are identity-based, not array-index-based
# ---------------------------------------------------------------------------
def test_predecessor_references_are_identity_based_not_positional() -> None:
    def build(order: tuple[str, ...]) -> CanonicalWorkflowDefinition:
        by_id = {
            "s_aaaa1111": (),
            "s_bbbb2222": ("s_aaaa1111",),
            "s_cccc3333": ("s_aaaa1111",),
        }
        steps = tuple(
            _v3_step(
                id=step_id,
                name=step_id,
                v2_name=step_id,
                predecessors=by_id[step_id],
            )
            for step_id in order
        )
        return CanonicalWorkflowDefinition(
            global_config={},
            global_options=WorkflowConfig.from_mapping({"global": {}}).global_options,
            steps=steps,
            dependency_mode="explicit",
            predecessors=by_id,
            execution_order=("s_aaaa1111", "s_bbbb2222", "s_cccc3333"),
            terminal_steps=("s_bbbb2222", "s_cccc3333"),
            source_version="confflow.workflow.v3",
        )

    first = build(("s_aaaa1111", "s_bbbb2222", "s_cccc3333"))
    reordered = build(("s_cccc3333", "s_aaaa1111", "s_bbbb2222"))

    # Same graph, expressed by identity, regardless of the array order.
    assert first.predecessors == reordered.predecessors
    assert dict(first.predecessors)["s_bbbb2222"] == ("s_aaaa1111",)
    assert all(ref in first.predecessors for steps in first.predecessors.values() for ref in steps)


# ---------------------------------------------------------------------------
# Immutability of the new value objects
# ---------------------------------------------------------------------------
def test_new_ir_fields_are_immutable() -> None:
    from dataclasses import FrozenInstanceError

    step = _v3_step()
    with pytest.raises(FrozenInstanceError):
        step.id = "s_zzzz9999"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# The V2 planner is byte-for-byte unchanged
# ---------------------------------------------------------------------------
def test_v2_planner_output_is_unchanged(tmp_path) -> None:
    import yaml

    input_path = tmp_path / "input.xyz"
    input_path.write_text("1\nseed\nH 0 0 0\n", encoding="utf-8")
    config = tmp_path / "workflow.yaml"
    config.write_text(yaml.safe_dump(V2_MAPPING, sort_keys=True), encoding="utf-8")

    plan = build_workflow_plan([str(input_path)], str(config))

    assert plan.predecessors == {"gen": [], "sp": ["gen"]}
    assert plan.execution_order == ["gen", "sp"]
    assert plan.terminal_steps == ["sp"]
    assert plan.steps[0]["name"] == "gen"
    assert "label" not in plan.steps[0]
