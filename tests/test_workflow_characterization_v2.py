#!/usr/bin/env python3

"""Frozen characterisation of V2 workflow semantics (R0/R1 baseline).

Every assertion here records behaviour that already ships on ``main``. The file
exists so the canonical-workflow-IR refactor can prove it did **not** change:

* graph resolution (implicit-linear vs explicit DAG, mixed ``inputs``),
* step identity (generated names, aliases, directory names),
* duplicate-dependency de-duplication,
* terminal-step detection,
* unknown/extension field handling, and
* the workflow fingerprint / ``workflow_binding.v1`` digest.

The fingerprint digests below are golden constants captured from the shipping
implementation. They must never be regenerated from a new implementation: if a
digest changes, the V2 resume identity changed and the refactor must stop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from confflow.config.canonical import (
    WORKFLOW_BINDING_SCHEMA,
    build_workflow_binding,
    workflow_fingerprint,
)
from confflow.config.canonical.schema import WORKFLOW_SCHEMA_VERSION
from confflow.config.canonical.types import WorkflowConfig
from confflow.workflow.plan import build_workflow_plan
from confflow.workflow.step_handlers import _resolve_chk_input_dir

# ---------------------------------------------------------------------------
# Golden digests captured from the shipping V2 implementation.
# ---------------------------------------------------------------------------
LINEAR_FINGERPRINT = "sha256:dcfdb66266972aa34df5565f68853ad26071ff4bd0456e7cc6dc125cc63e22da"
EXPLICIT_FINGERPRINT = "sha256:07b20e5eb600f44941de48cca30ba81a7cc8bf412ef87dfcf3fe26d3964944db"
UNNAMED_FINGERPRINT = "sha256:0ae149cb0b39aeccfb23ed3634de154864ce5b362854d96db25bd82ee276fabb"

LINEAR_MAPPING: dict[str, Any] = {
    "global": {"iprog": "orca", "itask": "sp", "total_memory": "4GB"},
    "steps": [
        {"name": "gen", "type": "confgen", "params": {"chains": ["1-2-3"]}},
        {"name": "sp", "type": "calc", "params": {"keyword": "HF"}},
    ],
}

EXPLICIT_MAPPING: dict[str, Any] = {
    "global": {},
    "steps": [
        {"name": "gen", "type": "confgen", "params": {"chains": ["1-2-3"]}},
        {"name": "a", "type": "confgen", "inputs": ["gen"], "params": {"chains": ["1-2"]}},
        {"name": "b", "type": "confgen", "inputs": ["gen"], "params": {"chains": ["1-2"]}},
    ],
}

UNNAMED_MAPPING: dict[str, Any] = {
    "global": {},
    "steps": [
        {"type": "gen", "params": {"chains": ["1-2"]}},
        {"type": "task", "params": {"keyword": "HF", "weird": 7}},
    ],
}


def _write_input(directory: Path, name: str = "input.xyz") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("1\nseed\nH 0 0 0\n", encoding="utf-8")
    return path


def _plan(tmp_path: Path, mapping: dict[str, Any], *, inputs: int = 1):
    input_paths = [_write_input(tmp_path, f"input{index}.xyz") for index in range(inputs)]
    config = tmp_path / "workflow.yaml"
    config.write_text(yaml.safe_dump(mapping, sort_keys=True), encoding="utf-8")
    return build_workflow_plan([str(path) for path in input_paths], str(config))


# ---------------------------------------------------------------------------
# A / B / C / D — graph resolution
# ---------------------------------------------------------------------------
def test_implicit_linear_steps_form_a_chain(tmp_path: Path) -> None:
    plan = _plan(
        tmp_path,
        {
            "global": {},
            "steps": [
                {"name": "A", "type": "confgen"},
                {"name": "B", "type": "confgen"},
                {"name": "C", "type": "confgen"},
            ],
        },
    )

    assert plan.predecessors == {"A": [], "B": ["A"], "C": ["B"]}
    assert plan.execution_order == ["A", "B", "C"]
    assert plan.terminal_steps == ["C"]


def test_explicit_dag_is_preserved(tmp_path: Path) -> None:
    plan = _plan(
        tmp_path,
        {
            "global": {},
            "steps": [
                {"name": "A", "type": "confgen"},
                {"name": "B", "type": "confgen", "inputs": ["A"]},
                {"name": "C", "type": "confgen", "inputs": ["A"]},
            ],
        },
    )

    assert plan.predecessors == {"A": [], "B": ["A"], "C": ["A"]}
    assert sorted(plan.terminal_steps) == ["B", "C"]


def test_explicit_empty_inputs_is_a_root(tmp_path: Path) -> None:
    plan = _plan(
        tmp_path,
        {
            "global": {},
            "steps": [
                {"name": "A", "type": "confgen", "inputs": []},
                {"name": "B", "type": "confgen", "inputs": ["A"]},
            ],
        },
    )

    assert plan.predecessors == {"A": [], "B": ["A"]}
    assert plan.terminal_steps == ["B"]


def test_any_declared_inputs_selects_explicit_dag_for_the_whole_workflow(
    tmp_path: Path,
) -> None:
    """A single ``inputs`` key flips the workflow into explicit DAG mode.

    Steps without ``inputs`` then become roots rather than following the
    document order, which is the shipping V2 rule.
    """
    plan = _plan(
        tmp_path,
        {
            "global": {},
            "steps": [
                {"name": "A", "type": "confgen"},
                {"name": "B", "type": "confgen", "inputs": ["A"]},
                {"name": "C", "type": "confgen"},
            ],
        },
    )

    assert plan.predecessors == {"A": [], "B": ["A"], "C": []}
    assert sorted(plan.execution_order) == ["A", "B", "C"]
    assert sorted(plan.terminal_steps) == ["B", "C"]


# ---------------------------------------------------------------------------
# H — duplicate dependencies
# ---------------------------------------------------------------------------
def test_duplicate_predecessors_are_deduped_preserving_order(tmp_path: Path) -> None:
    plan = _plan(
        tmp_path,
        {
            "global": {},
            "steps": [
                {"name": "left", "type": "confgen"},
                {"name": "right", "type": "confgen"},
                {
                    "name": "join",
                    "type": "confgen",
                    "inputs": ["left", "right", "left"],
                },
            ],
        },
    )

    assert plan.predecessors["join"] == ["left", "right"]


# ---------------------------------------------------------------------------
# E / G — step identity, generated names, aliases
# ---------------------------------------------------------------------------
def test_unnamed_steps_generate_type_index_names_and_dirnames(tmp_path: Path) -> None:
    plan = _plan(tmp_path, UNNAMED_MAPPING)

    assert [step["name"] for step in plan.steps] == ["confgen_1", "calc_2"]
    assert plan.step_dirnames == ["confgen_1", "calc_2"]
    assert list(plan.by_step_name) == ["confgen_1", "calc_2"]
    assert plan.name_to_dirname == {"confgen_1": "confgen_1", "calc_2": "calc_2"}


def test_step_type_aliases_normalise_to_canonical_types(tmp_path: Path) -> None:
    plan = _plan(
        tmp_path,
        {
            "global": {},
            "steps": [
                {"name": "g", "type": "gen", "params": {"chains": ["1-2"]}},
                {"name": "t", "type": "task", "params": {"keyword": "HF"}},
            ],
        },
    )

    assert [step["type"] for step in plan.steps] == ["confgen", "calc"]


# ---------------------------------------------------------------------------
# F — disabled steps stay in the plan
# ---------------------------------------------------------------------------
def test_disabled_step_remains_in_order_and_identity(tmp_path: Path) -> None:
    plan = _plan(
        tmp_path,
        {
            "global": {},
            "steps": [
                {"name": "one", "type": "confgen", "enabled": False},
                {"name": "two", "type": "confgen"},
            ],
        },
    )

    assert plan.execution_order == ["one", "two"]
    assert plan.by_step_name["one"]["enabled"] is False
    assert plan.terminal_steps == ["two"]


# ---------------------------------------------------------------------------
# I — terminal detection
# ---------------------------------------------------------------------------
def test_terminal_detection_explicit_multiple_terminals(tmp_path: Path) -> None:
    plan = _plan(tmp_path, EXPLICIT_MAPPING)

    assert sorted(plan.terminal_steps) == ["a", "b"]


# ---------------------------------------------------------------------------
# J — checkpoint reference (name and 1-based numeric position)
# ---------------------------------------------------------------------------
def test_chk_from_step_resolves_by_name_and_position() -> None:
    steps = [
        {"name": "first step", "type": "calc", "params": {"keyword": "HF"}},
        {"name": "second", "type": "calc", "params": {"keyword": "HF"}},
    ]

    assert _resolve_chk_input_dir({"chk_from_step": "first step"}, "/root", steps) == (
        "/root/first_step/backups"
    )
    assert _resolve_chk_input_dir({"chk_from_step": "1"}, "/root", steps) == (
        "/root/first_step/backups"
    )
    assert _resolve_chk_input_dir({"chk_from_step": "2"}, "/root", steps) == (
        "/root/second/backups"
    )
    assert _resolve_chk_input_dir({"chk_from_step": "99"}, "/root", steps) is None
    assert _resolve_chk_input_dir({"chk_from_step": "missing"}, "/root", steps) is None
    assert _resolve_chk_input_dir({}, "/root", steps) is None


# ---------------------------------------------------------------------------
# K — unknown / extension fields
# ---------------------------------------------------------------------------
def test_unknown_param_fields_participate_in_the_fingerprint(tmp_path: Path) -> None:
    with_extra = _plan(
        tmp_path / "with",
        {
            "global": {},
            "steps": [{"name": "cc", "type": "calc", "params": {"keyword": "HF", "weird": 7}}],
        },
    )
    clean = _plan(
        tmp_path / "clean",
        {"global": {}, "steps": [{"name": "cc", "type": "calc", "params": {"keyword": "HF"}}]},
    )

    assert workflow_fingerprint(with_extra) != workflow_fingerprint(clean)


def test_unknown_top_level_step_field_does_not_change_the_fingerprint(
    tmp_path: Path,
) -> None:
    with_note = _plan(
        tmp_path / "with",
        {
            "global": {},
            "steps": [
                {"name": "gen", "type": "confgen", "params": {"chains": ["1-2"]}, "note": "hello"}
            ],
        },
    )
    clean = _plan(
        tmp_path / "clean",
        {
            "global": {},
            "steps": [{"name": "gen", "type": "confgen", "params": {"chains": ["1-2"]}}],
        },
    )

    assert workflow_fingerprint(with_note) == workflow_fingerprint(clean)


def test_raw_mapping_is_preserved_on_the_typed_model() -> None:
    raw = {
        "global": {"keyword": "HF"},
        "steps": [{"name": "gen", "type": "confgen", "note": "kept"}],
    }
    model = WorkflowConfig.from_mapping(raw)

    assert model.raw == raw
    assert model.raw["steps"][0]["note"] == "kept"


# ---------------------------------------------------------------------------
# L — fingerprint / binding golden stability
# ---------------------------------------------------------------------------
def test_linear_fingerprint_golden(tmp_path: Path) -> None:
    plan = _plan(tmp_path, LINEAR_MAPPING)

    assert workflow_fingerprint(plan) == LINEAR_FINGERPRINT
    binding = build_workflow_binding(plan)
    assert binding.schema == WORKFLOW_BINDING_SCHEMA
    assert binding.workflow_schema == WORKFLOW_SCHEMA_VERSION
    assert binding.fingerprint == LINEAR_FINGERPRINT


def test_explicit_fingerprint_golden(tmp_path: Path) -> None:
    plan = _plan(tmp_path, EXPLICIT_MAPPING)

    assert workflow_fingerprint(plan) == EXPLICIT_FINGERPRINT


def test_unnamed_fingerprint_golden(tmp_path: Path) -> None:
    plan = _plan(tmp_path, UNNAMED_MAPPING)

    assert workflow_fingerprint(plan) == UNNAMED_FINGERPRINT


@pytest.mark.parametrize(
    "mapping",
    (
        LINEAR_MAPPING,
        EXPLICIT_MAPPING,
        UNNAMED_MAPPING,
    ),
)
def test_fingerprint_is_independent_of_runtime_inputs(tmp_path: Path, mapping) -> None:
    """The fingerprint binds workflow semantics only, never input paths."""
    first = _plan(tmp_path / "one", mapping)
    second = _plan(tmp_path / "two", mapping)

    assert workflow_fingerprint(first) == workflow_fingerprint(second)
