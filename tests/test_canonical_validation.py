#!/usr/bin/env python3

"""Regression coverage for the consolidated canonical workflow validation (R2).

Covers the definition/run-context split, the structured diagnostics, the frozen
``configuration-validation.v1`` CLI projection, and the consistency between what
``config validate`` rejects and what the planner actually fails on.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from confflow.config import cli as config_cli
from confflow.config.canonical import (
    Diagnostic,
    to_canonical_workflow,
    validate_workflow_definition,
    validate_workflow_run_context,
)
from confflow.config.canonical.types import WorkflowConfig
from confflow.core.contracts import ExitCode
from confflow.core.exceptions import ConfFlowError
from confflow.workflow.plan import build_workflow_plan


def _codes(diagnostics: list[Diagnostic]) -> set[str]:
    return {diagnostic.code for diagnostic in diagnostics}


def _errors(raw: dict[str, Any]) -> list[Diagnostic]:
    return [d for d in validate_workflow_definition(raw) if d.is_error]


def _definition(raw: dict[str, Any]):
    return to_canonical_workflow(WorkflowConfig.from_mapping(raw))


def _write_input(directory: Path, name: str = "input.xyz") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("1\nseed\nH 0 0 0\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1 / 2 / 17 / 18 / 19 — valid documents
# ---------------------------------------------------------------------------
def test_valid_implicit_linear_workflow() -> None:
    assert (
        _errors(
            {
                "global": {},
                "steps": [
                    {"name": "gen", "type": "confgen", "params": {"chains": ["1-2-3"]}},
                    {"name": "sp", "type": "calc", "params": {"keyword": "HF"}},
                ],
            }
        )
        == []
    )


def test_valid_explicit_dag() -> None:
    assert (
        _errors(
            {
                "global": {},
                "steps": [
                    {"name": "root", "type": "confgen", "params": {"chains": ["1-2"]}},
                    {
                        "name": "left",
                        "type": "confgen",
                        "inputs": ["root"],
                        "params": {"chains": ["1-2"]},
                    },
                    {
                        "name": "right",
                        "type": "confgen",
                        "inputs": ["root"],
                        "params": {"chains": ["1-2"]},
                    },
                    {
                        "name": "join",
                        "type": "confgen",
                        "inputs": ["left", "right"],
                        "params": {"chains": ["1-2"]},
                    },
                ],
            }
        )
        == []
    )


def test_explicit_root_inputs_empty_is_valid() -> None:
    assert (
        _errors(
            {
                "global": {},
                "steps": [
                    {"name": "r", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
                    {
                        "name": "c",
                        "type": "confgen",
                        "inputs": ["r"],
                        "params": {"chains": ["1-2"]},
                    },
                ],
            }
        )
        == []
    )


def test_v2_type_aliases_are_valid() -> None:
    assert (
        _errors(
            {
                "global": {},
                "steps": [
                    {"name": "g", "type": "gen", "params": {"chains": ["1-2"]}},
                    {"name": "t", "type": "task", "params": {"keyword": "HF"}},
                ],
            }
        )
        == []
    )


def test_unknown_extension_fields_are_valid() -> None:
    assert (
        _errors(
            {
                "global": {"unused": True},
                "tools": {"anything": 1},
                "steps": [
                    {
                        "name": "gen",
                        "type": "confgen",
                        "params": {"chains": ["1-2"]},
                        "note": "ignored",
                    }
                ],
            }
        )
        == []
    )


# ---------------------------------------------------------------------------
# 3 / 4 / 5 / 6 — graph definition errors
# ---------------------------------------------------------------------------
def test_unknown_predecessor_is_reported() -> None:
    diagnostics = _errors(
        {
            "steps": [
                {
                    "name": "child",
                    "type": "calc",
                    "inputs": ["missing"],
                    "params": {"keyword": "HF"},
                }
            ]
        }
    )

    assert _codes(diagnostics) == {"workflow.unknown_predecessor"}


def test_self_cycle_is_reported() -> None:
    diagnostics = _errors(
        {
            "steps": [
                {"name": "a", "type": "confgen", "inputs": ["a"], "params": {"chains": ["1-2"]}}
            ]
        }
    )

    assert _codes(diagnostics) == {"workflow.dependency_cycle"}


def test_multi_node_cycle_is_reported() -> None:
    diagnostics = _errors(
        {
            "steps": [
                {"name": "a", "type": "confgen", "inputs": ["b"], "params": {"chains": ["1-2"]}},
                {"name": "b", "type": "confgen", "inputs": ["a"], "params": {"chains": ["1-2"]}},
            ]
        }
    )

    assert _codes(diagnostics) == {"workflow.dependency_cycle"}


def test_duplicate_step_name_is_reported() -> None:
    diagnostics = _errors(
        {
            "steps": [
                {"name": "x", "type": "confgen", "params": {"chains": ["1-2"]}},
                {"name": "x", "type": "confgen", "params": {"chains": ["1-2"]}},
            ]
        }
    )

    assert _codes(diagnostics) == {"workflow.duplicate_step_name"}


# ---------------------------------------------------------------------------
# 7 / 8 / 16 — calc input cardinality
# ---------------------------------------------------------------------------
def test_calc_fan_in_is_a_definition_error() -> None:
    diagnostics = _errors(
        {
            "steps": [
                {"name": "l", "type": "confgen", "params": {"chains": ["1-2"]}},
                {"name": "r", "type": "confgen", "params": {"chains": ["1-2"]}},
                {"name": "j", "type": "calc", "inputs": ["l", "r"], "params": {"keyword": "HF"}},
            ]
        }
    )

    assert _codes(diagnostics) == {"workflow.calc_fan_in"}
    assert "exactly one input" in diagnostics[0].message


def test_single_predecessor_calc_is_valid() -> None:
    assert (
        _errors(
            {
                "steps": [
                    {"name": "conf", "type": "confgen", "params": {"chains": ["1-2"]}},
                    {"name": "sp", "type": "calc", "inputs": ["conf"], "params": {"keyword": "HF"}},
                ]
            }
        )
        == []
    )


def test_disabled_calc_fan_in_is_not_a_definition_error() -> None:
    assert (
        _errors(
            {
                "steps": [
                    {"name": "l", "type": "confgen", "params": {"chains": ["1-2"]}},
                    {"name": "r", "type": "confgen", "params": {"chains": ["1-2"]}},
                    {
                        "name": "j",
                        "type": "calc",
                        "enabled": False,
                        "inputs": ["l", "r"],
                        "params": {"keyword": "HF"},
                    },
                ]
            }
        )
        == []
    )


# ---------------------------------------------------------------------------
# 9 / 10 / 11 — calc semantics
# ---------------------------------------------------------------------------
def test_invalid_calc_program() -> None:
    diagnostics = _errors(
        {"steps": [{"name": "s", "type": "calc", "params": {"keyword": "HF", "iprog": "xtb"}}]}
    )

    assert _codes(diagnostics) == {"calc.invalid_program"}


def test_invalid_calc_task() -> None:
    diagnostics = _errors(
        {"steps": [{"name": "s", "type": "calc", "params": {"keyword": "HF", "itask": "scan"}}]}
    )

    assert _codes(diagnostics) == {"calc.invalid_task"}


@pytest.mark.parametrize("keyword", [None, "", "   "])
def test_missing_or_empty_keyword(keyword) -> None:
    params: dict[str, Any] = {"iprog": "orca", "itask": "sp"}
    if keyword is not None:
        params["keyword"] = keyword
    diagnostics = _errors({"steps": [{"name": "s", "type": "calc", "params": params}]})

    assert _codes(diagnostics) == {"calc.missing_keyword"}


# ---------------------------------------------------------------------------
# 12 — confgen semantics
# ---------------------------------------------------------------------------
def test_invalid_confgen_parameters() -> None:
    diagnostics = _errors(
        {"steps": [{"name": "g", "type": "confgen", "params": {"force_rotate": [1, 2]}}]}
    )

    assert _codes(diagnostics) == {"confgen.invalid"}


# ---------------------------------------------------------------------------
# 13 / 14 / 15 — chk_from_step
# ---------------------------------------------------------------------------
def test_invalid_chk_from_step_name() -> None:
    diagnostics = _errors(
        {
            "steps": [
                {"name": "a", "type": "calc", "params": {"keyword": "HF"}},
                {"name": "b", "type": "calc", "params": {"keyword": "HF", "chk_from_step": "nope"}},
            ]
        }
    )

    assert _codes(diagnostics) == {"workflow.chk_from_step_unknown"}


def test_invalid_chk_from_step_position() -> None:
    diagnostics = _errors(
        {
            "steps": [
                {"name": "a", "type": "calc", "params": {"keyword": "HF", "chk_from_step": "9"}}
            ]
        }
    )

    assert _codes(diagnostics) == {"workflow.chk_from_step_unknown"}


def test_valid_chk_from_step_by_name_and_position() -> None:
    assert (
        _errors(
            {
                "steps": [
                    {"name": "a", "type": "calc", "params": {"keyword": "HF"}},
                    {
                        "name": "b",
                        "type": "calc",
                        "params": {"keyword": "HF", "chk_from_step": "a"},
                    },
                    {
                        "name": "c",
                        "type": "calc",
                        "params": {"keyword": "HF", "chk_from_step": "1"},
                    },
                ]
            }
        )
        == []
    )


# ---------------------------------------------------------------------------
# Diagnostics model
# ---------------------------------------------------------------------------
def test_diagnostic_v1_projection_is_only_path_and_message() -> None:
    diagnostic = Diagnostic("calc.invalid_task", "error", "steps[1].params", "boom", "s")

    assert diagnostic.to_v1_issue() == {"path": "steps[1].params", "message": "boom"}


# ---------------------------------------------------------------------------
# Definition vs run-context boundary
# ---------------------------------------------------------------------------
def test_root_calc_is_definition_valid_but_run_context_sensitive() -> None:
    raw = {"global": {}, "steps": [{"name": "sp", "type": "calc", "params": {"keyword": "HF"}}]}

    # Definition validation never inspects the run context.
    assert _errors(raw) == []

    definition = _definition(raw)
    assert validate_workflow_run_context(definition, input_file_count=1) == []
    context_errors = validate_workflow_run_context(definition, input_file_count=2)
    assert _codes(context_errors) == {"workflow.input_cardinality"}


def test_config_validate_does_not_claim_to_check_the_run_context() -> None:
    # Two input files would break a root calc at run time, but stdin validation
    # has no input information, so it must not reject the document.
    raw = {"global": {}, "steps": [{"name": "sp", "type": "calc", "params": {"keyword": "HF"}}]}
    assert _errors(raw) == []


# ---------------------------------------------------------------------------
# 20 — CLI determinism and frozen v1 shape
# ---------------------------------------------------------------------------
def test_cli_json_diagnostics_are_deterministic(monkeypatch, capsys) -> None:
    payload = {
        "global": {},
        "steps": [
            {"name": "l", "type": "confgen", "params": {"chains": ["1-2"]}},
            {"name": "r", "type": "confgen", "params": {"chains": ["1-2"]}},
            {"name": "j", "type": "calc", "inputs": ["l", "r"], "params": {"keyword": "HF"}},
        ],
    }
    monkeypatch.setattr(config_cli.sys, "stdin", io.StringIO(json.dumps(payload)))
    assert config_cli.main(["validate", "--json", "--stdin"]) == ExitCode.USAGE_ERROR
    first = capsys.readouterr().out
    monkeypatch.setattr(config_cli.sys, "stdin", io.StringIO(json.dumps(payload)))
    assert config_cli.main(["validate", "--json", "--stdin"]) == ExitCode.USAGE_ERROR
    second = capsys.readouterr().out

    assert first == second
    document = json.loads(first)
    assert set(document) == {"schema", "valid", "workflow_schema_sha256", "issues"}
    assert document["valid"] is False
    assert all(set(issue) == {"path", "message"} for issue in document["issues"])


def test_cli_accepts_a_definition_valid_document(monkeypatch, capsys) -> None:
    payload = {
        "global": {},
        "steps": [{"name": "gen", "type": "confgen", "params": {"chains": ["1-2-3"]}}],
    }
    monkeypatch.setattr(config_cli.sys, "stdin", io.StringIO(json.dumps(payload)))

    assert config_cli.main(["validate", "--json", "--stdin"]) == ExitCode.SUCCESS
    document = json.loads(capsys.readouterr().out)
    assert document["valid"] is True
    assert document["issues"] == []


# ---------------------------------------------------------------------------
# Planner / validator consistency
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    (
        {
            "steps": [
                {"name": "c", "type": "calc", "inputs": ["missing"], "params": {"keyword": "HF"}}
            ]
        },
        {
            "steps": [
                {"name": "x", "type": "confgen", "params": {"chains": ["1-2"]}},
                {"name": "x", "type": "confgen", "params": {"chains": ["1-2"]}},
            ]
        },
        {
            "steps": [
                {"name": "a", "type": "confgen", "inputs": ["b"], "params": {"chains": ["1-2"]}},
                {"name": "b", "type": "confgen", "inputs": ["a"], "params": {"chains": ["1-2"]}},
            ]
        },
        {
            "steps": [
                {"name": "l", "type": "confgen", "params": {"chains": ["1-2"]}},
                {"name": "r", "type": "confgen", "params": {"chains": ["1-2"]}},
                {"name": "j", "type": "calc", "inputs": ["l", "r"], "params": {"keyword": "HF"}},
            ]
        },
    ),
)
def test_definition_invalid_reason_matches_the_planner(tmp_path: Path, raw: dict[str, Any]) -> None:
    import yaml

    diagnostics = _errors(raw)
    assert diagnostics, "expected the definition validator to reject this document"

    input_xyz = _write_input(tmp_path)
    config = tmp_path / "workflow.yaml"
    config.write_text(yaml.safe_dump(raw, sort_keys=True), encoding="utf-8")

    with pytest.raises(ConfFlowError) as caught:
        build_workflow_plan([str(input_xyz)], str(config))

    assert str(caught.value) == diagnostics[0].message


@pytest.mark.parametrize(
    "raw",
    (
        {
            "global": {},
            "steps": [{"name": "gen", "type": "confgen", "params": {"chains": ["1-2"]}}],
        },
        {
            "global": {},
            "steps": [
                {"name": "root", "type": "confgen", "params": {"chains": ["1-2"]}},
                {"name": "a", "type": "confgen", "inputs": ["root"], "params": {"chains": ["1-2"]}},
                {"name": "b", "type": "confgen", "inputs": ["root"], "params": {"chains": ["1-2"]}},
            ],
        },
        {
            "global": {},
            "steps": [
                {"name": "conf", "type": "confgen", "params": {"chains": ["1-2"]}},
                {"name": "sp", "type": "calc", "inputs": ["conf"], "params": {"keyword": "HF"}},
            ],
        },
    ),
)
def test_definition_valid_workflow_plans_without_semantic_errors(
    tmp_path: Path, raw: dict[str, Any]
) -> None:
    import yaml

    assert _errors(raw) == []
    input_xyz = _write_input(tmp_path)
    config = tmp_path / "workflow.yaml"
    config.write_text(yaml.safe_dump(raw, sort_keys=True), encoding="utf-8")

    plan = build_workflow_plan([str(input_xyz)], str(config))
    assert plan.execution_order


def test_run_context_error_matches_the_planner(tmp_path: Path) -> None:
    import yaml

    raw = {"global": {}, "steps": [{"name": "sp", "type": "calc", "params": {"keyword": "HF"}}]}
    inputs = [_write_input(tmp_path, "a.xyz"), _write_input(tmp_path, "b.xyz")]
    config = tmp_path / "workflow.yaml"
    config.write_text(yaml.safe_dump(raw, sort_keys=True), encoding="utf-8")

    context_errors = validate_workflow_run_context(_definition(raw), input_file_count=2)
    assert _codes(context_errors) == {"workflow.input_cardinality"}

    with pytest.raises(ConfFlowError) as caught:
        build_workflow_plan([str(path) for path in inputs], str(config))

    assert str(caught.value) == context_errors[0].message
