#!/usr/bin/env python3

"""Differential acceptance for the canonical workflow IR (R1).

For every V2 fixture these tests prove the new canonical pipeline produces the
same externally observable plan as the historical pipeline
(``WorkflowConfig.as_legacy_shape`` -> ``build_step_graph`` -> linear fallback):

* resolved global / step values,
* predecessors, execution order, terminal steps,
* step directory names and name->dirname mapping,
* the V2 execution projection the engine consumes.

A separate guard pins the raw-mapping boundary: the planner itself must no
longer reach for ``as_legacy_shape`` or ``.raw``; only the V2 adapter may.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest
import yaml

from confflow.config.canonical import to_canonical_workflow
from confflow.config.canonical.types import WorkflowConfig
from confflow.workflow.dag import build_step_graph, topo_order
from confflow.workflow.plan import build_workflow_plan
from confflow.workflow.step_naming import build_step_dir_name_map

CORPUS: dict[str, dict[str, Any]] = {
    "implicit_linear": {
        "global": {"iprog": "orca", "itask": "sp", "total_memory": "4GB"},
        "steps": [
            {"name": "A", "type": "confgen", "params": {"chains": ["1-2"]}},
            {"name": "B", "type": "confgen", "params": {"chains": ["1-2"]}},
            {"name": "C", "type": "calc", "params": {"keyword": "HF"}},
        ],
    },
    "explicit_diamond": {
        "global": {},
        "steps": [
            {"name": "root", "type": "confgen", "params": {"chains": ["1-2"]}},
            {"name": "left", "type": "confgen", "inputs": ["root"], "params": {"chains": ["1-2"]}},
            {"name": "right", "type": "confgen", "inputs": ["root"], "params": {"chains": ["1-2"]}},
            {
                "name": "join",
                "type": "confgen",
                "inputs": ["left", "right"],
                "params": {"chains": ["1-2"]},
            },
        ],
    },
    "explicit_empty_root": {
        "global": {},
        "steps": [
            {"name": "r", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
            {"name": "c", "type": "confgen", "inputs": ["r"], "params": {"chains": ["1-2"]}},
        ],
    },
    "mixed_inputs": {
        "global": {},
        "steps": [
            {"name": "A", "type": "confgen", "params": {"chains": ["1-2"]}},
            {"name": "B", "type": "confgen", "inputs": ["A"], "params": {"chains": ["1-2"]}},
            {"name": "C", "type": "confgen", "params": {"chains": ["1-2"]}},
        ],
    },
    "duplicate_dependencies": {
        "global": {},
        "steps": [
            {"name": "s", "type": "confgen", "params": {"chains": ["1-2"]}},
            {
                "name": "j",
                "type": "confgen",
                "inputs": ["s", "s"],
                "params": {"chains": ["1-2"]},
            },
        ],
    },
    "aliases": {
        "global": {},
        "steps": [
            {"name": "g", "type": "gen", "params": {"chains": ["1-2"]}},
            {"name": "t", "type": "task", "params": {"keyword": "HF"}},
        ],
    },
    "generated_names": {
        "global": {},
        "steps": [
            {"type": "gen", "params": {"chains": ["1-2"]}},
            {"type": "confgen", "params": {"chains": ["1-2"]}},
        ],
    },
    "whitespace_name": {
        "global": {},
        "steps": [
            {"name": "  padded  ", "type": "confgen", "params": {"chains": ["1-2"]}},
            {"name": "   ", "type": "confgen", "params": {"chains": ["1-2"]}},
        ],
    },
    "disabled_step": {
        "global": {},
        "steps": [
            {"name": "one", "type": "confgen", "enabled": False, "params": {"chains": ["1-2"]}},
            {"name": "two", "type": "confgen", "params": {"chains": ["1-2"]}},
        ],
    },
    "unknown_extensions": {
        "global": {"extra_global": "ignored"},
        "tools": {"anything": True},
        "steps": [
            {
                "name": "gen",
                "type": "confgen",
                "params": {"chains": ["1-2"]},
                "note": "kept-as-extension",
            }
        ],
    },
}


def _write_input(directory: Path, name: str = "input.xyz") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("1\nseed\nH 0 0 0\n", encoding="utf-8")
    return path


def _legacy_view(mapping: dict[str, Any]) -> dict[str, Any]:
    """Reproduce the pre-R1 planner semantics from the public legacy helpers."""
    workflow = WorkflowConfig.from_mapping(mapping)
    legacy = workflow.as_legacy_shape()
    steps = legacy["steps"]
    raw_predecessors, by_step_name, _ = build_step_graph(steps)
    explicit = any("inputs" in step for step in steps)
    if explicit:
        predecessors = raw_predecessors
    else:
        ordered = list(by_step_name)
        predecessors = {
            name: ([ordered[index - 1]] if index else []) for index, name in enumerate(ordered)
        }
    execution_order = [name for wave in topo_order(predecessors) for name in wave]
    if explicit:
        predecessor_names = {
            predecessor
            for step_predecessors in predecessors.values()
            for predecessor in step_predecessors
        }
        terminal_steps = [name for name in predecessors if name not in predecessor_names]
    else:
        terminal_steps = [execution_order[-1]]
    step_dirnames, _ = build_step_dir_name_map(steps)
    step_index_by_name = {name: index for index, name in enumerate(by_step_name)}
    name_to_dirname = {name: step_dirnames[index] for name, index in step_index_by_name.items()}
    return {
        "global": legacy["global"],
        "steps": steps,
        "predecessors": predecessors,
        "execution_order": execution_order,
        "terminal_steps": terminal_steps,
        "step_dirnames": step_dirnames,
        "name_to_dirname": name_to_dirname,
    }


@pytest.mark.parametrize("fixture_name", sorted(CORPUS))
def test_canonical_plan_matches_the_legacy_pipeline(tmp_path: Path, fixture_name: str) -> None:
    mapping = CORPUS[fixture_name]
    input_xyz = _write_input(tmp_path)
    config = tmp_path / "workflow.yaml"
    config.write_text(yaml.safe_dump(mapping, sort_keys=True), encoding="utf-8")

    view = _legacy_view(mapping)
    plan = build_workflow_plan([str(input_xyz)], str(config))

    assert plan.global_config == view["global"]
    assert plan.steps == view["steps"]
    assert plan.predecessors == view["predecessors"]
    assert plan.execution_order == view["execution_order"]
    assert plan.terminal_steps == view["terminal_steps"]
    assert plan.step_dirnames == view["step_dirnames"]
    assert plan.name_to_dirname == view["name_to_dirname"]


@pytest.mark.parametrize("fixture_name", sorted(CORPUS))
def test_canonical_execution_shape_equals_as_legacy_shape(fixture_name: str) -> None:
    workflow = WorkflowConfig.from_mapping(CORPUS[fixture_name])

    definition = to_canonical_workflow(workflow)
    global_config, steps = definition.to_v2_execution_shape()
    legacy = workflow.as_legacy_shape()

    assert global_config == legacy["global"]
    assert steps == legacy["steps"]


def test_ir_resolves_the_graph_fully() -> None:
    definition = to_canonical_workflow(WorkflowConfig.from_mapping(CORPUS["explicit_diamond"]))

    assert definition.dependency_mode == "explicit"
    assert definition.predecessors == {
        "root": (),
        "left": ("root",),
        "right": ("root",),
        "join": ("left", "right"),
    }
    assert definition.execution_order == ("root", "left", "right", "join")
    assert definition.terminal_steps == ("join",)
    assert [step.name for step in definition.steps] == ["root", "left", "right", "join"]


def test_ir_records_implicit_linear_mode_and_resolved_chain() -> None:
    definition = to_canonical_workflow(WorkflowConfig.from_mapping(CORPUS["implicit_linear"]))

    assert definition.dependency_mode == "implicit_linear"
    assert definition.predecessors == {"A": (), "B": ("A",), "C": ("B",)}
    assert definition.terminal_steps == ("C",)


def test_ir_preserves_unknown_extensions() -> None:
    definition = to_canonical_workflow(WorkflowConfig.from_mapping(CORPUS["unknown_extensions"]))

    assert definition.extensions == {"tools": {"anything": True}}
    step = definition.steps[0]
    assert step.extensions == {"note": "kept-as-extension"}


def test_ir_does_not_declare_inputs_for_implicit_steps() -> None:
    definition = to_canonical_workflow(WorkflowConfig.from_mapping(CORPUS["implicit_linear"]))

    assert all(step.inputs_declared is False for step in definition.steps)


def test_ir_is_immutable() -> None:
    from dataclasses import FrozenInstanceError

    definition = to_canonical_workflow(WorkflowConfig.from_mapping(CORPUS["implicit_linear"]))

    with pytest.raises(FrozenInstanceError):
        definition.dependency_mode = "explicit"  # type: ignore[misc]


def test_planner_does_not_read_raw_workflow() -> None:
    """The raw-mapping boundary must sit at the adapter, not the planner."""
    from confflow.workflow import plan as plan_module

    source = inspect.getsource(plan_module)
    assert "as_legacy_shape" not in source
    assert ".raw" not in source
    assert "build_step_graph" not in source
    assert "to_canonical_workflow" in source


def test_canonical_config_layer_does_not_import_the_workflow_package() -> None:
    """The canonical IR/adapter must stay below the workflow layer.

    ``from .workflow import ...`` (relative, same package) is fine; a relative
    import that climbs out to ``confflow.workflow`` is not, because that would
    make the low-level config layer depend on the execution layer.
    """
    import ast

    root = Path(__file__).parents[1] / "confflow" / "config" / "canonical"
    offenders: list[str] = []
    for path in (root / "workflow.py", root / "v2_adapter.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level >= 3:
                module = node.module or ""
                if module == "workflow" or module.startswith("workflow."):
                    offenders.append(f"{path.name}: {'.' * node.level}{module}")

    assert offenders == []
