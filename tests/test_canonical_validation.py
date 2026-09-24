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
    ValidationProfile,
    to_canonical_workflow,
    validate_workflow_definition,
    validate_workflow_run_context,
)
from confflow.config.canonical.types import WorkflowConfig
from confflow.core.contracts import ExitCode
from confflow.core.exceptions import ConfFlowError
from confflow.shared.confgen_params import resolve_confgen_params
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
# Runnable confgen invariants (T1 / T2 / T3 / T4 / T5 / T6)
# ---------------------------------------------------------------------------
def test_t1_runnable_confgen_without_chains_is_invalid() -> None:
    diagnostics = _errors(
        {"global": {}, "steps": [{"name": "conf", "type": "confgen", "params": {}}]}
    )

    assert _codes(diagnostics) == {"confgen.chains.required"}
    assert diagnostics[0].severity == "error"
    assert diagnostics[0].step_ref == "conf"
    assert "chains" in diagnostics[0].message


def test_t2_legacy_chain_alias_is_valid() -> None:
    assert (
        _errors(
            {
                "global": {},
                "steps": [{"name": "conf", "type": "confgen", "params": {"chain": "1-2-3-4"}}],
            }
        )
        == []
    )


def test_t3_canonical_chains_list_is_valid() -> None:
    assert (
        _errors(
            {
                "global": {},
                "steps": [{"name": "conf", "type": "confgen", "params": {"chains": ["1-2-3-4"]}}],
            }
        )
        == []
    )


@pytest.mark.parametrize("angle_step", [0, -60])
def test_t4_t5_non_positive_angle_step_is_invalid(angle_step: int) -> None:
    diagnostics = _errors(
        {
            "global": {},
            "steps": [
                {
                    "name": "conf",
                    "type": "confgen",
                    "params": {"chains": ["1-2-3-4"], "angle_step": angle_step},
                }
            ],
        }
    )

    assert _codes(diagnostics) == {"confgen.angle_step.invalid"}
    assert diagnostics[0].step_ref == "conf"


def test_t6_positive_angle_step_is_valid() -> None:
    assert (
        _errors(
            {
                "global": {},
                "steps": [
                    {
                        "name": "conf",
                        "type": "confgen",
                        "params": {"chains": ["1-2-3-4"], "angle_step": 60},
                    }
                ],
            }
        )
        == []
    )


def test_t8_legacy_facade_projects_canonical_confgen_invariants() -> None:
    # The facade must derive these from the canonical diagnostics, not from a
    # second copy of the chains / angle_step rules.
    from confflow.shared.config_validation import validate_step_config

    missing = validate_step_config({"name": "conf", "type": "confgen", "params": {}}, 0)
    assert any("chains" in error for error in missing)

    angle = validate_step_config(
        {
            "name": "conf",
            "type": "confgen",
            "params": {"chains": ["1-2-3-4"], "angle_step": 0},
        },
        0,
    )
    assert any("angle_step" in error for error in angle)


def test_t9_cli_rejects_missing_chains_with_exact_wire_shape(monkeypatch, capsys) -> None:
    payload = {"global": {}, "steps": [{"name": "conf", "type": "confgen", "params": {}}]}
    monkeypatch.setattr(config_cli.sys, "stdin", io.StringIO(json.dumps(payload)))

    assert config_cli.main(["validate", "--json", "--stdin"]) == ExitCode.USAGE_ERROR
    document = json.loads(capsys.readouterr().out)

    assert set(document) == {"schema", "valid", "workflow_schema_sha256", "issues"}
    assert document["valid"] is False
    assert document["issues"]
    for issue in document["issues"]:
        assert set(issue) == {"path", "message"}


def test_definition_valid_confgen_satisfies_generator_preconditions() -> None:
    """A definition-valid confgen step must not fail on chains/angle_step later.

    The generator requires non-empty chains (``generator.py`` "use --chain to
    specify rotation chains") and builds ``range(0, 360, int(angle_step))``
    (``rotations.py``), so a runnable definition must guarantee both.
    """
    for params in ({"chain": "1-2-3-4"}, {"chains": ["1-2-3-4"], "angle_step": 60}):
        raw = {"global": {}, "steps": [{"name": "conf", "type": "confgen", "params": params}]}
        assert _errors(raw) == []

        definition = _definition(raw)
        resolved = resolve_confgen_params(
            definition.steps[0].params,
            default_workers=definition.global_options.max_parallel_jobs,
        )

        assert resolved["chains"]
        assert int(resolved["angle_step"]) > 0
        assert list(range(0, 360, int(resolved["angle_step"])))


# ---------------------------------------------------------------------------
# chk_from_step
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


# ---------------------------------------------------------------------------
# Version-aware façade (R3.4 review) — one public entry, exact V2 compatibility
# ---------------------------------------------------------------------------
V2_SCHEMA = "confflow.workflow.v2"
V3_SCHEMA = "confflow.workflow.v3"


def _tuples(diagnostics: list[Diagnostic]) -> list[tuple[str, str, str, str, str | None]]:
    return [(d.code, d.severity, d.path, d.message, d.step_ref) for d in diagnostics]


def _v3_doc(steps: list[dict[str, Any]], **root: Any) -> dict[str, Any]:
    document: dict[str, Any] = {"schema": V3_SCHEMA, "steps": steps}
    document.update(root)
    return document


_V3_LINEAR = [
    {"id": "s001", "type": "confgen", "inputs": [], "params": {"chains": ["1-2"]}},
    {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
]

# A frozen regression corpus: same V2 input → exactly these diagnostics. This is
# the pre/post-façade pin — the legacy body was moved verbatim, and any drift in
# code, severity, path, message or step_ref fails here.
_V2_CORPUS: dict[str, tuple[dict[str, Any], list[tuple[str, str, str, str, str | None]]]] = {
    "valid_linear": (
        {
            "global": {},
            "steps": [
                {"name": "gen", "type": "confgen", "params": {"chains": ["1-2"]}},
                {"name": "calc", "type": "calc", "inputs": ["gen"], "params": {"keyword": "HF"}},
            ],
        },
        [],
    ),
    "duplicate_step_name": (
        {
            "global": {},
            "steps": [
                {"name": "gen", "type": "confgen", "params": {"chains": ["1-2"]}},
                {"name": "gen", "type": "confgen", "params": {"chains": ["1-2"]}},
            ],
        },
        [
            (
                "workflow.duplicate_step_name",
                "error",
                "",
                "workflow step names must be unique; duplicate name: 'gen'",
                "gen",
            )
        ],
    ),
    "unknown_predecessor": (
        {
            "global": {},
            "steps": [
                {"name": "calc", "type": "calc", "inputs": ["nope"], "params": {"keyword": "HF"}},
            ],
        },
        [
            (
                "workflow.unknown_predecessor",
                "error",
                "",
                "workflow step 'calc' has unknown predecessor(s): 'nope'",
                "calc",
            )
        ],
    ),
    "calc_fan_in": (
        {
            "global": {},
            "steps": [
                {"name": "gen1", "type": "confgen", "params": {"chains": ["1-2"]}},
                {"name": "gen2", "type": "confgen", "params": {"chains": ["1-2"]}},
                {
                    "name": "calc",
                    "type": "calc",
                    "inputs": ["gen1", "gen2"],
                    "params": {"keyword": "HF"},
                },
            ],
        },
        [
            (
                "workflow.calc_fan_in",
                "error",
                "steps[3]",
                "calc step 'calc' has 2 inputs; a calc step accepts exactly one input. "
                "Add a confgen step to merge them first.",
                "calc",
            )
        ],
    ),
    "confgen_missing_chains": (
        {"global": {}, "steps": [{"name": "gen", "type": "confgen", "params": {}}]},
        [
            (
                "confgen.chains.required",
                "error",
                "steps[1].params.chains",
                "confgen step requires non-empty 'chains' (or its alias 'chain') naming "
                "the bonds to rotate",
                "gen",
            )
        ],
    ),
    "calc_missing_keyword": (
        {"global": {}, "steps": [{"name": "calc", "type": "calc", "params": {"itask": "opt"}}]},
        [
            (
                "calc.missing_keyword",
                "error",
                "steps[1].params",
                "calc step requires a non-empty keyword",
                "calc",
            )
        ],
    ),
    "chk_unknown": (
        {
            "global": {},
            "steps": [
                {
                    "name": "gen",
                    "type": "confgen",
                    "params": {"chains": ["1-2"], "chk_from_step": "ghost"},
                },
            ],
        },
        [
            (
                "workflow.chk_from_step_unknown",
                "error",
                "steps[1].params.chk_from_step",
                "step 'gen' references an unknown chk_from_step: 'ghost'",
                "gen",
            )
        ],
    ),
}


def test_v8_v2_regression_corpus_exact_diagnostics() -> None:
    for name, (raw, expected) in _V2_CORPUS.items():
        assert _tuples(validate_workflow_definition(raw)) == expected, name


def test_v1_schema_absent_is_v2_with_exact_legacy_diagnostics() -> None:
    raw = _V2_CORPUS["confgen_missing_chains"][0]
    assert _tuples(validate_workflow_definition(raw)) == _V2_CORPUS["confgen_missing_chains"][1]
    assert _tuples(validate_workflow_definition(raw)) == _tuples(
        validate_workflow_definition({**raw, "schema": V2_SCHEMA})
    )


def test_v2_explicit_v2_schema_takes_the_same_v2_path() -> None:
    raw = {**_V2_CORPUS["calc_fan_in"][0], "schema": V2_SCHEMA}
    assert _tuples(validate_workflow_definition(raw)) == _V2_CORPUS["calc_fan_in"][1]


def test_v3_valid_document_has_no_diagnostics() -> None:
    assert validate_workflow_definition(_v3_doc(_V3_LINEAR)) == []


def test_v3_semantic_invalid_document_reports_v3_diagnostics() -> None:
    raw = _v3_doc(
        [
            {"id": "s001", "type": "confgen", "inputs": [], "params": {}},
            {"id": "s002", "type": "calc", "inputs": ["s001"], "params": {"keyword": "HF"}},
        ]
    )
    codes = [diagnostic.code for diagnostic in validate_workflow_definition(raw)]
    assert codes == ["confgen.chains.required"]


def test_v5_unknown_schema_fails_closed() -> None:
    diagnostics = validate_workflow_definition({"schema": "confflow.workflow.v4", "steps": []})
    assert [
        (diagnostic.code, diagnostic.path, diagnostic.message) for diagnostic in diagnostics
    ] == [("workflow.schema", "schema", "unsupported workflow schema: 'confflow.workflow.v4'")]
    # Non-string schema values fail closed the same way.
    diagnostics = validate_workflow_definition({"schema": 123, "steps": []})
    assert diagnostics[0].code == "workflow.schema"
    assert diagnostics[0].path == "schema"


def test_v6_v3_fragment_with_fragment_profile_passes() -> None:
    raw = _v3_doc([{"type": "confgen", "inputs": [], "params": {}}])
    diagnostics = validate_workflow_definition(raw, profile=ValidationProfile.FRAGMENT)
    assert diagnostics == []


def test_v7_same_fragment_under_runnable_profile_errors() -> None:
    raw = _v3_doc([{"type": "confgen", "inputs": [], "params": {}}])
    diagnostics = validate_workflow_definition(raw)
    assert diagnostics
    assert all(diagnostic.is_error for diagnostic in diagnostics)


def test_facade_non_mapping_input_keeps_the_legacy_diagnostic() -> None:
    assert _tuples(validate_workflow_definition("nope")) == [
        ("workflow.root_not_mapping", "error", "", "workflow config root must be a mapping", None)
    ]
