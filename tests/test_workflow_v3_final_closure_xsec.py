"""Final Closure cross-section tests: X1, X2, X5 (X3 in P1A-4, X4 in contract v3).

X1: R5 structured metadata produces a valid, executable V3 calc config, and
    ``params.theory``/``keyword`` disagreement is a runnable validation error.
X2: A V3 recipe fragment instantiates to opaque IDs and runs the full
    authoring chain (parse, runnable validation, dataflow, dry-run).
X5: Duplicate labels with an explicit ID DAG and disabled bypass remain legal
    and executable; labels stay display-only snapshots.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from confflow.config.canonical.validation import ValidationProfile, validate_workflow_v3
from confflow.workflow.plan import WorkflowV3Plan, build_workflow_plan
from confflow.workflow.v3_dataflow import validate_effective_dataflow_v3
from confflow.workflow.v3_runtime import run_v3_workflow
from tests.test_workflow_v3_runtime import _FakeHandlers, _write_config, _write_xyz

# Hermetic CI: fake orca/g16 entrypoints on PATH (real files, real identity).
pytestmark = pytest.mark.usefixtures("fake_qc_executables_on_path")


def _runnable_errors(doc: dict[str, Any]) -> list[str]:
    return [
        diagnostic.message
        for diagnostic in validate_workflow_v3(doc, profile=ValidationProfile.RUNNABLE)
        if diagnostic.severity == "error"
    ]


# ---------------------------------------------------------------------------
# X1: structured metadata -> valid + executable V3 calc config
# ---------------------------------------------------------------------------
def test_x1_theory_bearing_v3_calc_executes(tmp_path: Path, monkeypatch) -> None:
    """A V3 calc step carrying both ``theory`` and ``keyword`` validates and runs."""
    handlers = _FakeHandlers(monkeypatch)
    steps = [
        {
            "id": "s001",
            "label": "Opt",
            "type": "calc",
            "inputs": [],
            "params": {
                "iprog": "g16",
                "itask": "opt",
                "keyword": "B3LYP/def2-SVP opt",
                "theory": {
                    "program": "g16",
                    "task": "opt",
                    "method": "B3LYP",
                    "basis": "def2-SVP",
                },
            },
        },
    ]
    doc = {"schema": "confflow.workflow.v3", "steps": steps}
    assert _runnable_errors(doc) == []

    _write_xyz(tmp_path / "input.xyz")
    config = _write_config(tmp_path / "wf.yaml", steps)
    work = tmp_path / "work"
    run_v3_workflow(
        input_xyz=[str(tmp_path / "input.xyz")],
        config_file=str(config),
        work_dir=str(work),
    )
    state = json.loads((work / ".workflow_state.json").read_text(encoding="utf-8"))
    assert state["final_status"] == "completed"
    assert [call["step_name"] for call in handlers.calc_calls] == ["s001"]
    manifest = json.loads((work / "output_manifest.json").read_text(encoding="utf-8"))
    assert manifest["terminals"] == [
        {"id": "s001", "label": "Opt", "artifacts": ["steps/s001/result.xyz"]}
    ]


def test_x1_theory_keyword_mismatch_is_a_runnable_error(tmp_path: Path) -> None:
    """Structured fields disagreeing with the raw keyword fail runnable validation."""
    doc = {
        "schema": "confflow.workflow.v3",
        "steps": [
            {
                "id": "s001",
                "type": "calc",
                "inputs": [],
                "params": {
                    "keyword": "B3LYP/def2-SVP opt freq",
                    "theory": {"task": "opt", "method": "B3LYP", "basis": "def2-SVP"},
                },
            },
        ],
    }
    errors = _runnable_errors(doc)
    assert len(errors) == 1
    assert "freq" in errors[0]


def test_x1_theory_without_keyword_defers_to_presence_rules(tmp_path: Path) -> None:
    """A fragment template carrying theory but no keyword stays fragment-valid."""
    doc = {
        "schema": "confflow.workflow.v3",
        "steps": [
            {
                "type": "calc",
                "inputs": [],
                "params": {"theory": {"task": "opt", "method": "B3LYP"}},
            },
        ],
    }
    fragment_issues = validate_workflow_v3(doc, profile=ValidationProfile.FRAGMENT)
    assert [d for d in fragment_issues if d.severity == "error"] == []


# ---------------------------------------------------------------------------
# X2: recipe -> parse -> validate -> effective dataflow -> dry-run
# ---------------------------------------------------------------------------
def test_x2_recipe_with_pinned_fields_runs_the_full_authoring_chain(
    tmp_path: Path, capsys: Any
) -> None:
    """The Optimize recipe becomes runnable through the producer-owned chain."""
    import random
    import re

    from confflow.config.canonical.recipes import (
        RECIPE_STEP_ID_PATTERN,
        build_recipe_catalog_v3,
        instantiate_recipe_v3,
    )
    from confflow.config.canonical.structured import compile_structured_calc
    from confflow.config.canonical.v3_parser import parse_v3_document
    from confflow.workflow.dry_run import run_dry_run

    catalog = build_recipe_catalog_v3()
    recipe = next(item for item in catalog["recipes"] if item["id"] == "optimize")
    assert recipe["required_fields"] == ["calc.program", "calc.keyword"]
    # Recipes are FRAGMENT-profile partials: no final step identity retained.
    assert "id" not in recipe["document"]["steps"][0]

    params = compile_structured_calc(
        {"method": "B3LYP", "basis": "def2-SVP"}, program="g16", task="opt"
    )
    assert params["iprog"] == "g16"
    assert params["itask"] == "opt"
    assert "theory" in params

    template = copy.deepcopy(recipe["document"])
    template["steps"][0]["params"].update(params)
    steps = instantiate_recipe_v3(template, existing_ids=(), rng=random.Random(42))
    assert len(steps) == 1
    assert re.fullmatch(RECIPE_STEP_ID_PATTERN, steps[0]["id"])
    document = {"schema": "confflow.workflow.v3", "global": {}, "steps": steps}

    definition = parse_v3_document(document)
    assert [step.id for step in definition.steps] == [steps[0]["id"]]
    assert _runnable_errors(document) == []

    _write_xyz(tmp_path / "input.xyz")
    config = tmp_path / "wf.yaml"
    config.write_text(json.dumps(document), encoding="utf-8")
    plan = build_workflow_plan([str(tmp_path / "input.xyz")], str(config))
    assert isinstance(plan, WorkflowV3Plan)
    assert validate_effective_dataflow_v3(plan, external_input_count=1) == ()

    work = tmp_path / "work"
    run_dry_run([str(tmp_path / "input.xyz")], str(config), str(work))
    out = capsys.readouterr().out
    assert steps[0]["id"] in out
    assert not (work / ".workflow_state.json").exists()
    assert not (work / "steps").exists()


# ---------------------------------------------------------------------------
# X5: duplicate labels + explicit DAG + disabled bypass stay executable
# ---------------------------------------------------------------------------
def test_x5_duplicate_labels_with_disabled_bypass_execute(tmp_path: Path, monkeypatch) -> None:
    """Identity is the stable ID: three steps may share one label and still run."""
    handlers = _FakeHandlers(monkeypatch)
    steps = [
        {
            "id": "s001",
            "label": "Same",
            "type": "confgen",
            "inputs": [],
            "params": {"chains": ["1-2"]},
        },
        {
            "id": "s002",
            "label": "Same",
            "type": "calc",
            "enabled": False,
            "inputs": ["s001"],
            "params": {"keyword": "HF"},
        },
        {
            "id": "s003",
            "label": "Same",
            "type": "calc",
            "inputs": ["s002"],
            "params": {"keyword": "HF"},
        },
    ]
    doc = {"schema": "confflow.workflow.v3", "steps": steps}
    assert _runnable_errors(doc) == []
    plan = _write_and_plan(tmp_path, steps)
    assert validate_effective_dataflow_v3(plan, external_input_count=1) == ()

    work = tmp_path / "work"
    run_v3_workflow(
        input_xyz=[str(tmp_path / "input.xyz")],
        config_file=str(tmp_path / "wf.yaml"),
        work_dir=str(work),
    )
    assert [call["step_name"] for call in handlers.calc_calls] == ["s003"]
    assert handlers.calc_calls[0]["current_input"].endswith("steps/s001/search.xyz")

    manifest = json.loads((work / "output_manifest.json").read_text(encoding="utf-8"))
    # s003 is enabled so it publishes its own output; the bypass is visible in
    # its *input* (s001's artifact, asserted above), not in the terminal entry.
    # (Disabled-terminal forwarding to the effective producer is R6-10.)
    assert manifest["terminals"] == [
        {"id": "s003", "label": "Same", "artifacts": ["steps/s003/result.xyz"]}
    ]
    stats = json.loads((work / "workflow_stats.json").read_text(encoding="utf-8"))
    assert [step["label"] for step in stats["steps"]] == ["Same", "Same", "Same"]
    assert [step["id"] for step in stats["steps"]] == ["s001", "s002", "s003"]


def _write_and_plan(tmp_path: Path, steps: list[dict[str, Any]]) -> WorkflowV3Plan:
    _write_xyz(tmp_path / "input.xyz")
    config = _write_config(tmp_path / "wf.yaml", steps)
    plan = build_workflow_plan([str(tmp_path / "input.xyz")], str(config))
    assert isinstance(plan, WorkflowV3Plan)
    return plan
