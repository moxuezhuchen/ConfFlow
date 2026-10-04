#!/usr/bin/env python3

"""A required input of zero structures fails; it is never a silent success (A1).

* assembly (A1-2): a required structure port is not satisfied by artifacts or
  results that happen to sit beside an empty structure set;
* ``confflow v4 run`` (A1-3): a run that cannot assemble a step ends with a
  non-zero exit code and a JSON report naming the reason, not a traceback.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from confflow import v4cli
from confflow.domain import FrozenDict, StructureSet
from confflow.domain.completion import StepStatus
from confflow.workflow.v4 import MaterializedOutputs, StepOutputs, assemble_work_items
from tests.v4._builders import (
    calc_step,
    checkpoint_set,
    compile_doc,
    run_inputs,
    structure_set,
    v4_doc,
)

FAKE_ORCA = Path(__file__).parent / "fakes" / "fake_orca.py"
STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}


# -- A1-2: assembly ------------------------------------------------------------


def test_artifacts_do_not_satisfy_an_empty_required_structure_port() -> None:
    doc = v4_doc(
        [
            calc_step("s_opt", bindings={"structure": {"source": {"run": "structures"}}}),
            calc_step(
                "s_freq",
                bindings={"structure": {"source": {"step": "s_opt", "port": "structures"}}},
            ),
        ],
        inputs=STRUCTURE_INPUTS,
    )
    plan = compile_doc(doc).plan
    assert plan is not None
    producer = StepOutputs(
        step_id="s_opt",
        structures=StructureSet(),
        artifacts=checkpoint_set("s0", producer_step_id="s_opt"),
        status=StepStatus.COMPLETED,
    )
    assembly = assemble_work_items(
        plan,
        run_inputs(structures={"structures": structure_set("s0")}),
        materialized=MaterializedOutputs(steps=FrozenDict({"s_opt": producer})),
    )
    messages = [item.message for item in assembly.errors if item.step_id == "s_freq"]
    assert messages == [
        "required structure port 'structure' has no values "
        "(step 's_opt' published no structure for it)"
    ]
    assert assembly.for_step("s_freq") == ()


# -- A1-3: `confflow v4 run` ------------------------------------------------------

_CONFGEN_STEP = """
  - id: s_gen
    executor: confgen
    bindings:
      structure: {source: {run: structures}}
    confgen:
      schema_version: 3
      index_base: 1
      paths:
        - {start: 2, end: 5, move: end, angles: [0.0, 120.0]}
"""
_DOWNSTREAM = {
    "refine": """
  - id: s_next
    executor: structure_transform
    bindings:
      structure: {source: {step: s_gen, port: structures}}
    transform: {kind: refine, native: {rmsd_threshold_angstrom: 0.25}}
""",
    "deduplicate": """
  - id: s_next
    executor: structure_transform
    bindings:
      structure: {source: {step: s_gen, port: structures}}
    transform: {kind: deduplicate}
""",
    "orca": """
  - id: s_next
    executor: calculation
    bindings:
      structure: {source: {step: s_gen, port: structures}}
    calculation:
      program: orca
      role: opt
      native: {keyword: "B3LYP def2-SVP Opt"}
      checks: [normal_termination, geometry_required]
""",
}


def _run_cli(tmp_path: Path, capsys: Any, downstream: str) -> tuple[int, dict[str, Any]]:
    workflow = tmp_path / "flow.yaml"
    workflow.write_text(
        "schema: confflow.workflow.v4\n"
        "inputs:\n  structures: {kind: structure, cardinality: many}\n"
        "global:\n  scientific_defaults: {charge: 0, multiplicity: 1}\n"
        "  resources: {cores_per_item: 1, memory_per_item: 1GiB}\n"
        "steps:" + _CONFGEN_STEP + _DOWNSTREAM[downstream],
        encoding="utf-8",
    )
    xyz = tmp_path / "chain.xyz"
    xyz.write_text(
        "6\ncollinear carbons\n" + "".join(f"C {1.5 * i} 0.0 0.0\n" for i in range(6)),
        encoding="utf-8",
    )
    code = v4cli.main(
        [
            "run",
            "--workflow",
            str(workflow),
            "--inputs",
            f"structures={xyz}",
            "--run-root",
            str(tmp_path / "run"),
            "--executable",
            f"orca={FAKE_ORCA}",
            "--json",
        ]
    )
    return code, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("downstream", sorted(_DOWNSTREAM))
def test_a_consumer_with_no_input_structures_fails_with_a_json_reason(
    tmp_path: Path, capsys: Any, downstream: str
) -> None:
    code, report = _run_cli(tmp_path, capsys, downstream)
    assert code != 0
    assert report["status"] == "failed"
    failures = report["failures"]
    blocked = [item for item in failures if item["code"] == "run_not_executable"]
    assert len(blocked) == 1
    assert blocked[0]["message"] == (
        "step 's_next' is not assemblable and never executes: cardinality_error: "
        "required structure port 'structure' has no values "
        "(step 's_gen' published no structure for it)"
    )
    assert {item["id"] for item in report["steps"]} == {"s_gen"}
