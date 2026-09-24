"""R3.5 — Workflow V3 planning: WorkflowV3Plan, version dispatch and V2 freeze.

``build_workflow_plan`` is the version-aware planning façade. These tests pin:

- V3 documents plan into the planning-only ``WorkflowV3Plan`` on the R3.4
  validated graph (never a second graph implementation);
- every RUNNABLE-invalid V3 document refuses to plan;
- the V2 ``WorkflowPlan`` — fields, ordering, dirnames, fingerprint — is
  byte-for-byte unchanged by the dispatch.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from confflow.config.canonical import (
    WORKFLOW_SCHEMA_VERSION_V3,
    workflow_definition_fingerprint_v3,
)
from confflow.core.exceptions import ConfFlowError
from confflow.workflow.plan import (
    WorkflowPlan,
    WorkflowV3Plan,
    build_workflow_plan,
    workflow_plan_source_version,
)

V3 = "confflow.workflow.v3"


def _write_xyz(path: Path, label: str = "seed") -> Path:
    path.write_text(f"1\n{label}\nH 0 0 0\n", encoding="utf-8")
    return path


def _write_config(path: Path, steps: list[dict[str, Any]], **root: Any) -> Path:
    document: dict[str, Any] = {"schema": V3, "steps": steps}
    document.update(root)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def _linear(steps: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    return (
        steps
        if steps is not None
        else [
            {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
        ]
    )


def _plan(tmp_path: Path, steps: list[dict[str, Any]], **root: Any) -> WorkflowV3Plan:
    xyz = _write_xyz(tmp_path / "input.xyz")
    config = _write_config(tmp_path / "workflow.yaml", steps, **root)
    plan = build_workflow_plan([str(xyz)], str(config))
    assert isinstance(plan, WorkflowV3Plan)
    return plan


# ---------------------------------------------------------------------------
# PL1–PL8 — planning representation
# ---------------------------------------------------------------------------
def test_pl1_valid_linear_v3_plans(tmp_path: Path) -> None:
    plan = _plan(tmp_path, _linear())
    assert plan.source_version == WORKFLOW_SCHEMA_VERSION_V3
    assert [step.id for step in plan.steps] == ["s001", "s002"]
    assert plan.topological_order == ("s001", "s002")
    assert plan.roots == ("s001",)
    assert plan.terminals == ("s002",)
    assert plan.definition_fingerprint.startswith("sha256:")
    # The fingerprint is the frozen R3.4 Definition Fingerprint A.
    assert plan.definition_fingerprint == workflow_definition_fingerprint_v3(plan.definition)


def test_pl2_branching_dag_plan_graph(tmp_path: Path) -> None:
    plan = _plan(
        tmp_path,
        [
            {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
            {"id": "s003", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
        ],
    )
    assert plan.roots == ("s001",)
    assert plan.terminals == ("s002", "s003")
    by_id = {step.id: step for step in plan.steps}
    assert by_id["s002"].inputs == ("s001",)
    assert by_id["s003"].inputs == ("s001",)


def test_pl3_topological_wave_uses_stable_id_order(tmp_path: Path) -> None:
    # Document order deliberately scrambled; the wave order must not follow it.
    plan = _plan(
        tmp_path,
        [
            {"id": "s010", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {"id": "s002", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {
                "id": "s030",
                "type": "confgen",
                "inputs": ["s002", "s010"],
                "params": {"chains": ["1-2"]},
            },
        ],
    )
    assert plan.topological_order == ("s002", "s010", "s030")
    assert [step.id for step in plan.steps] == ["s002", "s010", "s030"]


def test_pl4_document_reorder_keeps_plan_order_and_fingerprint(tmp_path: Path) -> None:
    steps = [
        {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
        {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
        {"id": "s003", "type": "calc", "inputs": ["s002"], "params": {"keyword": "HF"}},
    ]
    (tmp_path / "a").mkdir()
    first = _plan(tmp_path / "a", copy.deepcopy(steps))
    (tmp_path / "b").mkdir()
    second = _plan(tmp_path / "b", list(reversed(copy.deepcopy(steps))))
    assert first.topological_order == second.topological_order
    assert first.definition_fingerprint == second.definition_fingerprint
    assert [step.id for step in first.steps] == [step.id for step in second.steps]


def test_pl5_duplicate_labels_plan_and_stay_distinct(tmp_path: Path) -> None:
    plan = _plan(
        tmp_path,
        [
            {
                "id": "s001",
                "type": "confgen",
                "inputs": [],
                "label": "same",
                "params": {"chains": ["1-2"]},
            },
            {
                "id": "s002",
                "type": "calc",
                "inputs": ["s001"],
                "label": "same",
                "params": {"keyword": "HF"},
            },
        ],
    )
    labels = [step.label for step in plan.steps]
    assert labels == ["same", "same"]
    assert len({step.id for step in plan.steps}) == 2


def test_pl6_checkpoint_preserved_in_plan(tmp_path: Path) -> None:
    plan = _plan(
        tmp_path,
        [
            {"id": "s001", "type": "calc", "inputs": [], "params": {"keyword": "HF"}},
            {
                "id": "s002",
                "type": "calc",
                "inputs": ["s001"],
                "params": {"keyword": "HF"},
                "checkpoint": {"from_step": "s001"},
            },
        ],
    )
    assert plan.steps[1].checkpoint_from == "s001"
    assert plan.steps[0].checkpoint_from is None


def test_pl7_disabled_step_represented_declared_graph_unchanged(tmp_path: Path) -> None:
    plan = _plan(
        tmp_path,
        [
            {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {
                "id": "s002",
                "type": "calc",
                "enabled": False,
                "inputs": ["s001"],
                "params": {"itask": "opt"},
            },
            {"id": "s003", "type": "calc", "inputs": ["s002"], "params": {"keyword": "HF"}},
        ],
    )
    by_id = {step.id: step for step in plan.steps}
    assert by_id["s002"].enabled is False
    # Declared graph only — effective bypass dataflow is R6.
    assert by_id["s003"].inputs == ("s002",)
    assert plan.topological_order == ("s001", "s002", "s003")


def test_pl8_root_inputs_empty_is_external_input_relation(tmp_path: Path) -> None:
    plan = _plan(tmp_path, _linear())
    assert plan.steps[0].inputs == ()
    assert plan.roots == ("s001",)


# ---------------------------------------------------------------------------
# PL9/PL10 — invalid documents never plan
# ---------------------------------------------------------------------------
def test_pl9_semantically_invalid_v3_does_not_plan(tmp_path: Path) -> None:
    xyz = _write_xyz(tmp_path / "input.xyz")
    # A cycle is schema-valid but semantically invalid.
    config = _write_config(
        tmp_path / "workflow.yaml",
        [
            {"id": "s001", "type": "calc", "inputs": ["s002"], "params": {"keyword": "HF"}},
            {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
        ],
    )
    try:
        build_workflow_plan([str(xyz)], str(config))
    except ConfFlowError as exc:
        assert "dependency_cycle" in str(exc)
    else:
        raise AssertionError("cyclic V3 document must not plan")
    # Unknown extensions and missing params are equally refused.
    for bad_steps in (
        [
            {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {
                "id": "s002",
                "type": "calc",
                "inputs": ["s001"],
                "params": {"keyword": "HF"},
                "extensions": {"vendor.unknown": {"x": 1}},
            },
        ],
        [{"id": "s001", "type": "calc", "inputs": [], "params": {"itask": "opt"}}],
    ):
        bad_config = _write_config(tmp_path / "bad.yaml", bad_steps)
        try:
            build_workflow_plan([str(xyz)], str(bad_config))
        except ConfFlowError:
            pass
        else:
            raise AssertionError("RUNNABLE-invalid V3 document must not plan")


def test_pl10_fragment_does_not_plan(tmp_path: Path) -> None:
    xyz = _write_xyz(tmp_path / "input.xyz")
    # No ids at all: a fragment shape, not a runnable document. The public
    # planning façade stays on the RUNNABLE profile, so the structural schema
    # (which requires ids) refuses it before any semantic question arises.
    config = _write_config(
        tmp_path / "workflow.yaml",
        [{"type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}}],
    )
    try:
        build_workflow_plan([str(xyz)], str(config))
    except ConfFlowError as exc:
        assert "id" in str(exc)
    else:
        raise AssertionError("a V3 fragment must not plan")


# ---------------------------------------------------------------------------
# V2 planning regression — the dispatch must not move V2 semantics
# ---------------------------------------------------------------------------
def test_v2_plan_regression_through_version_dispatch(tmp_path: Path) -> None:
    input_xyz = _write_xyz(tmp_path / "input.xyz")
    original_xyz = _write_xyz(tmp_path / "original.xyz", "original")
    config = tmp_path / "workflow.yaml"
    config.write_text(
        "global:\n"
        "  force_consistency: true\n"
        "steps:\n"
        "  - name: join output\n"
        "    type: confgen\n"
        "    inputs: [left, right]\n"
        "    params: {keyword: HF}\n"
        "  - name: right\n"
        "    type: confgen\n"
        "    inputs: [root]\n"
        "  - name: left\n"
        "    type: confgen\n"
        "    inputs: [root]\n"
        "  - name: root\n"
        "    type: confgen\n",
        encoding="utf-8",
    )

    plan = build_workflow_plan(
        [str(input_xyz)], str(config), original_input_files=[str(original_xyz)]
    )

    assert isinstance(plan, WorkflowPlan)
    assert workflow_plan_source_version(plan) == "confflow.workflow.v2"
    assert plan.input_files == [str(input_xyz.resolve())]
    assert plan.original_inputs == [str(original_xyz.resolve())]
    assert [step["name"] for step in plan.steps] == ["join output", "right", "left", "root"]
    assert plan.execution_order == ["root", "left", "right", "join output"]
    assert plan.terminal_steps == ["join output"]
    assert plan.step_dirnames == ["join_output", "right", "left", "root"]
    assert plan.name_to_dirname == {
        "join output": "join_output",
        "right": "right",
        "left": "left",
        "root": "root",
    }


def test_v2_plan_with_chk_from_step_keeps_legacy_shape(tmp_path: Path) -> None:
    input_xyz = _write_xyz(tmp_path / "input.xyz")
    config = tmp_path / "workflow.yaml"
    config.write_text(
        "steps:\n"
        "  - name: gen\n"
        "    type: confgen\n"
        "  - name: opt\n"
        "    type: calc\n"
        "    inputs: [gen]\n"
        "    params: {keyword: HF, chk_from_step: gen}\n",
        encoding="utf-8",
    )
    plan = build_workflow_plan([str(input_xyz)], str(config))
    assert isinstance(plan, WorkflowPlan)
    assert plan.steps[1]["params"]["chk_from_step"] == "gen"
    assert plan.step_dirnames == ["gen", "opt"]
