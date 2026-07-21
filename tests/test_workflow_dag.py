"""Pure-Python tests for the minimal workflow DAG helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
from confflow.core.exceptions import ConfFlowError
from confflow.workflow.dag import build_step_graph, topo_order
from confflow.workflow.engine import run_workflow
from confflow.workflow.step_handlers import StepExecutionResult


def test_build_step_graph_normalizes_a_chain() -> None:
    predecessors, by_name, declared_inputs = build_step_graph(
        [
            {"name": "prepare", "type": "confgen"},
            {"name": "optimize", "type": "calc", "inputs": "prepare"},
            {"name": "report", "type": "calc", "inputs": ["optimize"]},
        ]
    )

    assert list(by_name) == ["prepare", "optimize", "report"]
    assert predecessors == {
        "prepare": [],
        "optimize": ["prepare"],
        "report": ["optimize"],
    }
    assert declared_inputs == predecessors


def test_topo_order_handles_a_diamond() -> None:
    predecessors = {
        "root": [],
        "left": ["root"],
        "right": ["root"],
        "join": ["left", "right"],
    }

    assert topo_order(predecessors) == [["root"], ["left", "right"], ["join"]]


def test_topo_order_is_deterministic_for_ready_steps() -> None:
    predecessors = {
        "join": ["right", "left"],
        "right": ["root"],
        "left": ["root"],
        "root": [],
    }

    assert topo_order(predecessors) == [["root"], ["left", "right"], ["join"]]


def test_build_step_graph_rejects_duplicate_names() -> None:
    with pytest.raises(ConfFlowError, match="duplicate name: 'same'"):
        build_step_graph([{"name": "same"}, {"name": "same"}])


def test_build_step_graph_rejects_unknown_predecessors() -> None:
    with pytest.raises(ConfFlowError, match="unknown predecessor.*'missing'"):
        build_step_graph([{"name": "child", "inputs": ["missing"]}])


def test_topo_order_rejects_cycles() -> None:
    with pytest.raises(ConfFlowError, match="dependency cycle"):
        topo_order({"first": ["second"], "second": ["first"]})


def _write_xyz(path, label: str) -> None:
    path.write_text(f"1\n{label}\nH 0 0 0\n", encoding="utf-8")


def _fake_confgen_factory(seen: dict, outputs: dict):
    def fake_confgen(step_dir, current_input, params, input_files, global_config=None):
        del params, input_files, global_config
        step_path = Path(step_dir)
        name = step_path.name
        seen[name] = current_input
        output = step_path / "search.xyz"
        step_path.mkdir(parents=True, exist_ok=True)
        _write_xyz(output, name)
        outputs[name] = str(output)
        return StepExecutionResult(output_path=str(output))

    return fake_confgen


def test_engine_preserves_fan_out_and_fan_in_lineage(tmp_path, monkeypatch) -> None:
    input_xyz = tmp_path / "input.xyz"
    _write_xyz(input_xyz, "seed")
    config = tmp_path / "workflow.yaml"
    config.write_text(
        "global: {}\n"
        "steps:\n"
        "  - name: join\n"
        "    type: confgen\n"
        "    inputs: [left, right]\n"
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
    seen: dict = {}
    outputs: dict = {}
    monkeypatch.setattr(
        "confflow.workflow.engine._run_confgen_step",
        _fake_confgen_factory(seen, outputs),
    )

    stats = run_workflow([str(input_xyz)], str(config), str(tmp_path / "run"))

    assert [step["name"] for step in stats["steps"]] == ["root", "left", "right", "join"]
    assert seen["root"] == str(input_xyz.resolve())
    assert seen["left"] == outputs["root"]
    assert seen["right"] == outputs["root"]
    assert seen["join"] == [outputs["left"], outputs["right"]]
    assert stats["final_output"] == outputs["join"]


def test_engine_mixed_inputs_make_implicit_steps_roots(tmp_path, monkeypatch) -> None:
    input_xyz = tmp_path / "input.xyz"
    _write_xyz(input_xyz, "seed")
    config = tmp_path / "workflow.yaml"
    config.write_text(
        "global: {}\n"
        "steps:\n"
        "  - name: later_root\n"
        "    type: confgen\n"
        "  - name: child\n"
        "    type: confgen\n"
        "    inputs: [source]\n"
        "  - name: source\n"
        "    type: confgen\n",
        encoding="utf-8",
    )
    seen: dict = {}
    outputs: dict = {}
    monkeypatch.setattr(
        "confflow.workflow.engine._run_confgen_step",
        _fake_confgen_factory(seen, outputs),
    )

    run_workflow([str(input_xyz)], str(config), str(tmp_path / "run"))

    assert seen["later_root"] == str(input_xyz.resolve())
    assert seen["source"] == str(input_xyz.resolve())
    assert seen["child"] == outputs["source"]


def test_engine_keeps_linear_fallback_without_inputs(tmp_path, monkeypatch) -> None:
    input_xyz = tmp_path / "input.xyz"
    _write_xyz(input_xyz, "seed")
    config = tmp_path / "workflow.yaml"
    config.write_text(
        "global: {}\n"
        "steps:\n"
        "  - name: first\n"
        "    type: confgen\n"
        "  - name: second\n"
        "    type: confgen\n",
        encoding="utf-8",
    )
    seen: dict = {}
    outputs: dict = {}
    monkeypatch.setattr(
        "confflow.workflow.engine._run_confgen_step",
        _fake_confgen_factory(seen, outputs),
    )

    stats = run_workflow([str(input_xyz)], str(config), str(tmp_path / "run"))

    assert [step["name"] for step in stats["steps"]] == ["first", "second"]
    assert seen["first"] == str(input_xyz.resolve())
    assert seen["second"] == outputs["first"]


def test_engine_keeps_disabled_linear_steps_as_passthrough(tmp_path, monkeypatch) -> None:
    input_xyz = tmp_path / "input.xyz"
    _write_xyz(input_xyz, "seed")
    config = tmp_path / "workflow.yaml"
    config.write_text(
        "global: {}\n"
        "steps:\n"
        "  - name: disabled\n"
        "    type: confgen\n"
        "    enabled: false\n"
        "  - name: second\n"
        "    type: confgen\n",
        encoding="utf-8",
    )
    seen: dict = {}
    outputs: dict = {}
    monkeypatch.setattr(
        "confflow.workflow.engine._run_confgen_step",
        _fake_confgen_factory(seen, outputs),
    )

    run_workflow([str(input_xyz)], str(config), str(tmp_path / "run"))

    assert seen["second"] == str(input_xyz.resolve())


def test_engine_rejects_invalid_graph_before_executing_steps(tmp_path, monkeypatch) -> None:
    input_xyz = tmp_path / "input.xyz"
    _write_xyz(input_xyz, "seed")
    config = tmp_path / "workflow.yaml"
    config.write_text(
        "global: {}\n"
        "steps:\n"
        "  - name: first\n"
        "    type: confgen\n"
        "    inputs: [second]\n"
        "  - name: second\n"
        "    type: confgen\n"
        "    inputs: [first]\n",
        encoding="utf-8",
    )
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("invalid graph must be rejected before execution")

    monkeypatch.setattr("confflow.workflow.engine._run_confgen_step", fail_if_called)

    with pytest.raises(ConfFlowError, match="dependency cycle"):
        run_workflow([str(input_xyz)], str(config), str(tmp_path / "run"))
    assert called is False
