"""MLIP step wiring: schema, parser block map, registry contract, results source."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from confflow.execution.contracts import ExecutorCapability
from confflow.execution.registry import default_registry
from confflow.workflow.v4.compiler import compile_workflow
from confflow.workflow.v4.parser import parse_workflow_document
from confflow.workflow.v4.schema import MlipModel, MlipRelaxModel, StepModel


def _mlip_doc(mlip: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    block: dict[str, Any] = {
        "model": "uma-s-1p2p1",
        "mode": "sp_then_relax",
        "pick": {"lowest": 8, "per_label": 2},
    }
    if mlip is not None:
        block = mlip
    uma_step: dict[str, Any] = {
        "id": "uma",
        "executor": "mlip",
        "bindings": {"structure": {"source": {"step": "confgen", "port": "structures"}}},
        "mlip": block,
    }
    uma_step.update(overrides)
    return {
        "schema": "confflow.workflow.v4",
        "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
        "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
        "steps": [
            {
                "id": "confgen",
                "executor": "confgen",
                "bindings": {"structure": {"source": {"run": "structures"}}},
                "confgen": {"schema_version": 4, "seed": 1, "search": {"starts": 8}},
            },
            uma_step,
            {
                "id": "select",
                "executor": "structure_transform",
                "bindings": {
                    "structure": {"source": {"step": "uma", "port": "structures"}},
                    "results": {"source": {"step": "uma", "port": "results"}},
                },
                "transform": {"kind": "filter", "native": {"energy_window_kcal": 5.0}},
            },
        ],
    }


def _messages(result: Any) -> list[str]:
    return [str(diagnostic) for diagnostic in result.diagnostics]


def test_mlip_funnel_document_validates_and_fills_native_defaults() -> None:
    compiled = compile_workflow(_mlip_doc())
    assert compiled.ok, _messages(compiled)
    planned = {step.step_id: step for step in compiled.plan.steps}
    assert planned["uma"].executor is ExecutorCapability.MLIP
    assert dict(planned["uma"].scientific.native) == {
        "model": "uma-s-1p2p1",
        "task": "omol",
        "mode": "sp_then_relax",
        "index_base": 1,
        "relax": {
            "optimizer": "lbfgs",
            "fmax": 0.02,
            "max_steps": 600,
            "fix_distances": [],
        },
        "pick": {"lowest": 8, "per_label": 2},
        "reject_bond_stretch": None,
    }


def test_mlip_sp_mode_fills_pick_as_none_in_native() -> None:
    compiled = compile_workflow(_mlip_doc({"model": "uma-s-1p2p1", "mode": "sp"}))
    assert compiled.ok, _messages(compiled)
    planned = {step.step_id: step for step in compiled.plan.steps}
    native = dict(planned["uma"].scientific.native)
    assert native["pick"] is None
    assert native["relax"] == MlipRelaxModel().model_dump(mode="json")


def test_mlip_registry_contract_is_registered() -> None:
    registry = default_registry()
    contract = registry.executor(ExecutorCapability.MLIP)
    assert contract.contract_version == "confflow.contract.executor.mlip.v1"
    assert [port.name for port in contract.input_ports] == ["structure"]
    assert [port.name for port in contract.output_ports] == ["structures", "results", "artifacts"]
    artifacts = contract.output_ports[2]
    assert set(artifacts.roles) == {"mlip_job", "mlip_results", "mlip_log", "mlip_structures"}
    assert ExecutorCapability.MLIP.value in registry.capability_names


def test_sp_then_relax_requires_pick() -> None:
    compiled = compile_workflow(_mlip_doc({"model": "uma", "mode": "sp_then_relax"}))
    assert not compiled.ok
    assert any("pick" in message for message in _messages(compiled))
    with pytest.raises(ValidationError, match="requires a pick block"):
        MlipModel(model="uma", mode="sp_then_relax")


def test_pick_is_rejected_outside_sp_then_relax() -> None:
    with pytest.raises(ValidationError, match="pick is only valid"):
        MlipModel(model="uma", mode="relax", pick={"lowest": 2})


def test_relax_settings_rejected_in_sp_mode() -> None:
    with pytest.raises(ValidationError, match="relax settings need mode relax or sp_then_relax"):
        MlipModel(model="uma", mode="sp", relax={"fmax": 0.05})
    # Explicitly default-valued relax is indistinguishable from absent and accepted.
    assert MlipModel(model="uma", mode="sp", relax={}).relax == MlipRelaxModel()


@pytest.mark.parametrize(
    "block",
    [
        {"model": "uma", "mode": "relax", "relax": {"optimizer": "bfgs"}},
        {"model": "uma", "mode": "relax", "relax": {"fmax": 0}},
        {"model": "uma", "mode": "relax", "relax": {"max_steps": 0}},
        {"model": "uma", "mode": "relax", "relax": {"fix_distances": [[1, 2], [2, 1]]}},
        {"model": "uma", "mode": "relax", "relax": {"fix_distances": [[3, 3]]}},
        {"model": "uma", "mode": "relax", "relax": {"fix_distances": [[-1, 2]]}},
        {"model": "uma", "mode": "relax", "reject_bond_stretch": 1.0},
        {"model": "uma", "mode": "relax", "reject_bond_stretch": 0.9},
        {"model": "uma", "mode": "relax", "index_base": 2},
        {"model": "uma", "mode": "relax", "index_base": True},
        {"model": "", "mode": "relax"},
        {"mode": "relax"},
        {"model": "uma", "mode": "fire_and_forget"},
        {"model": "uma", "mode": "relax", "unknown_key": 1},
        {"model": "uma", "mode": "sp_then_relax", "pick": {"lowest": 0, "per_label": 0}},
        {"model": "uma", "mode": "sp_then_relax", "pick": {"lowest": -1}},
    ],
)
def test_bad_mlip_blocks_are_rejected(block: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        MlipModel.model_validate(block)


def test_step_model_rejects_unknown_mlip_key() -> None:
    with pytest.raises(ValidationError):
        StepModel.model_validate(
            {
                "id": "uma",
                "executor": "mlip",
                "mlip": {"model": "uma", "mode": "relax", "bogus": True},
            }
        )


def test_mlip_block_on_calculation_step_is_rejected() -> None:
    doc = copy.deepcopy(_mlip_doc())
    doc["steps"][0] = {
        "id": "confgen",
        "executor": "calculation",
        "bindings": {"structure": {"source": {"run": "structures"}}},
        "calculation": {"program": "gaussian", "native": {"keyword": "B3LYP"}},
        "mlip": {"model": "uma", "mode": "relax"},
    }
    parsed = parse_workflow_document(doc)
    assert parsed.definition is None
    assert any("must not declare blocks" in message for message in _messages(parsed))


def test_missing_mlip_block_reports_missing_executor_block() -> None:
    doc = _mlip_doc()
    del doc["steps"][1]["mlip"]
    parsed = parse_workflow_document(doc)
    assert parsed.definition is None
    assert any("require an mlip block" in message for message in _messages(parsed))


def test_script_args_and_outputs_rejected_on_mlip_step() -> None:
    parsed = parse_workflow_document(_mlip_doc(args=["--x"]))
    assert parsed.definition is None
    assert any("script args/outputs" in message for message in _messages(parsed))


def test_filter_results_bound_to_mlip_step_must_match_structure_source() -> None:
    doc = _mlip_doc()
    doc["steps"][2]["bindings"]["structure"] = {"source": {"step": "confgen", "port": "structures"}}
    compiled = compile_workflow(doc)
    assert not compiled.ok
    assert any("results" in message and "mlip" in message for message in _messages(compiled))


def test_filter_results_from_mlip_step_is_a_valid_energy_source() -> None:
    compiled = compile_workflow(_mlip_doc())
    assert compiled.ok, _messages(compiled)
    edges = {
        (edge.target_step_id, edge.target_port.name): edge.source_step_id
        for edge in compiled.plan.graph.edges
    }
    assert edges[("select", "results")] == "uma"
    assert edges[("select", "structure")] == "uma"


def test_fix_distances_accept_pair_and_pair_with_target() -> None:
    relax = MlipRelaxModel.model_validate(
        {"fix_distances": [[1, 2], [3, 4, 1.1], [5, 6, 2], [7, 8, 1.5e0]]}
    )
    assert [tuple(entry) for entry in relax.fix_distances] == [
        (1, 2),
        (3, 4, 1.1),
        (5, 6, 2),
        (7, 8, 1.5),
    ]


@pytest.mark.parametrize(
    "entry",
    [
        [1, 2, 0],
        [1, 2, 0.0],
        [1, 2, -1.2],
        [1, 2, float("nan")],
        [1, 2, float("inf")],
        [1, 2, True],
        [1, 2, "1.1"],
        [1, 2, 1.1, 3],
        [1, 2.0, 1.1],
        [-1, 2, 1.1],
        [3, 3, 1.1],
    ],
)
def test_bad_fix_distance_entries_are_rejected(entry: list[Any]) -> None:
    with pytest.raises(ValidationError):
        MlipRelaxModel.model_validate({"fix_distances": [entry]})


@pytest.mark.parametrize("second", [[2, 1], [2, 1, 1.2]])
def test_duplicate_pair_is_rejected_whatever_the_target(second: list[Any]) -> None:
    with pytest.raises(ValidationError, match="duplicates"):
        MlipRelaxModel.model_validate({"fix_distances": [[1, 2, 1.1], second]})
