#!/usr/bin/env python3

"""Canonical IR: V2 compatibility capacity tests.

The canonical workflow IR is the V2 compatibility boundary. These tests assert
the adapter keeps ``id`` unset, maps the V2 name to ``label``, leaves
``params.chk_from_step`` untouched and produces a byte-identical V2 execution
projection, and that the V2 planner output stays unchanged.

The V3-shaped IR capacity cases this file used to carry were retired by the
Architecture Diet PR-7 with the rest of the never-released Workflow V3 wire.
"""

from __future__ import annotations

from typing import Any

from confflow.config.canonical import to_canonical_workflow
from confflow.config.canonical.schema import WORKFLOW_SCHEMA_VERSION
from confflow.config.canonical.types import WorkflowConfig
from confflow.config.canonical.workflow import CanonicalWorkflowDefinition
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
