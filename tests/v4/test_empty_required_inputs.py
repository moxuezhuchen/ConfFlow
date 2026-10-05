#!/usr/bin/env python3

"""A required input of zero structures fails; it is never a silent success (A1).

Three layers, one regression family:

* assembly: a required structure port is not satisfied by artifacts or results
  that happen to sit beside an empty structure set (A1-2);
* ConfGen: a v3 run that realized no structure fails with the ledger vocabulary
  (A1-1);
* ``confflow v4 run``: every downstream shape of such a run ends with a non-zero
  exit code and a JSON report naming the reason, never a traceback or a
  ``completed`` run that executed nothing (A1-3).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from confflow import v4cli
from confflow.domain import FrozenDict, StructureRecord, StructureSet
from confflow.domain.completion import StepStatus, WorkItemStatus
from confflow.execution.confgen_executor import ConfgenExecutor
from confflow.workflow.v4 import MaterializedOutputs, StepOutputs, assemble_work_items
from confflow.workflow.v4.confgen_schema import ConfgenModelV3
from tests.v4._builders import (
    calc_step,
    checkpoint_set,
    compile_doc,
    run_inputs,
    structure_set,
    v4_doc,
)
from tests.v4._helpers.repair import _ctx, _item, _sci

FAKE_ORCA = Path(__file__).parent / "fakes" / "fake_orca.py"
STRUCTURE_INPUTS = {"structures": {"kind": "structure", "cardinality": "many"}}
NO_REALIZED = "confgen_no_realized_structures"


def _chain(spacing_x: float, zigzag: float, count: int = 6) -> StructureRecord:
    return StructureRecord(
        id="chain",
        atoms=("C",) * count,
        coordinates=tuple((i * spacing_x, zigzag * (i % 2), 0.0) for i in range(count)),
        charge=0,
        multiplicity=1,
    )


def _confgen_wire() -> dict[str, Any]:
    return ConfgenModelV3.model_validate(
        {
            "schema_version": 3,
            "index_base": 1,
            "paths": [{"start": 2, "end": 5, "move": "end", "angles": [0.0, 120.0]}],
        }
    ).scientific_native()


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


# -- A1-1: the ConfGen executor --------------------------------------------------


def _execute(record: StructureRecord) -> Any:
    item = _item("c1:g1", "c1", [record])
    sci = _sci(seed=None, native=FrozenDict(_confgen_wire()))
    return ConfgenExecutor().execute(item, _ctx(sci, tempfile.mkdtemp()))


def test_confgen_that_realized_nothing_fails_with_the_ledger_vocabulary() -> None:
    result = _execute(_chain(1.5, 0.0))  # collinear: the dihedral frames are ambiguous
    assert result.status is WorkItemStatus.FAILED
    assert len(result.structures) == 0
    (diagnostic,) = [d for d in result.diagnostics if d.code == NO_REALIZED]
    assert diagnostic.severity.value == "error"
    assert diagnostic.message == (
        "confgen v3 realized no structure: 0 of 8 raw targets published "
        "(target outcomes: UNRESOLVED=8; anomalies: AMBIGUOUS_KEY=8); "
        "see the ensemble report for the per-target ledger"
    )
    assert dict(diagnostic.details["target_categories"]) == {"UNRESOLVED": 8}
    assert not [d for d in result.diagnostics if d.code == "confgen_completed"]


def test_confgen_that_realized_structures_still_completes() -> None:
    result = _execute(_chain(1.5, 0.4))
    assert result.status is WorkItemStatus.COMPLETED
    assert len(result.structures) == 8
    assert not [d for d in result.diagnostics if d.code == NO_REALIZED]


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
    "none": "",
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
def test_a_run_whose_confgen_realized_nothing_fails_with_a_json_reason(
    tmp_path: Path, capsys: Any, downstream: str
) -> None:
    code, report = _run_cli(tmp_path, capsys, downstream)
    assert code != 0
    assert report["status"] == "failed"
    steps = {item["id"]: item["status"] for item in report["steps"]}
    assert steps["s_gen"] == "failed"
    assert "completed" not in steps.values()
    failures = report["failures"]
    assert any(item["code"] == NO_REALIZED and item["step_id"] == "s_gen" for item in failures)
    if downstream != "none":
        blocked = [item for item in failures if item["code"] == "run_not_executable"]
        assert len(blocked) == 1
        assert blocked[0]["message"].startswith(
            "step 's_next' is not assemblable and never executes"
        )
