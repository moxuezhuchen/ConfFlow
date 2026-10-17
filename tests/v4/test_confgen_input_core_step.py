#!/usr/bin/env python3

"""Confgen step with coordination shape ``input`` end to end (input-core WP6).

A user-level v4 declaration with ``shapes: ["input"]`` runs through the same executor and worker
path as ``test_confgen_search_step``: the item is launched as the search worker subprocess with the
fake xTB binding. The CN7 Ti salan fixture is rejected by the ideal-shape path (4 to 6 sites) and
accepted under the input shape.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from confflow.domain import FrozenDict
from confflow.domain.structure import StructureRecord
from confflow.workflow.v4.compiler import compile_workflow
from confflow.workflow.v4.confgen_schema import ConfgenModelV3
from tests.v4._dg_search_helpers import _write_fake
from tests.v4.test_confgen_search_step import _run, _scope, _summary

_INPUT_CORE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "confgen" / "coordination" / "input_core"
)
_BUDGET = {"budget": 16, "starts": 2, "release": False, "arrangement": "keep",
           "embed_timeout_seconds": 20, "max_cycles": 100, "bond_scale": 1.25}  # fmt: skip


def _fixture(name: str) -> tuple[dict[str, Any], StructureRecord]:
    """Return (index entry, neutral-or-charged StructureRecord) of one input-core fixture."""
    meta = json.loads((_INPUT_CORE / "index.json").read_text(encoding="utf-8"))[name]
    lines = (_INPUT_CORE / f"{name}.xyz").read_text(encoding="utf-8").splitlines()
    natoms = int(lines[0])
    rows = [line.split() for line in lines[2 : 2 + natoms]]
    record = StructureRecord(
        id=name,
        atoms=tuple(row[0] for row in rows),
        coordinates=tuple((float(row[1]), float(row[2]), float(row[3])) for row in rows),
        charge=int(meta["charge"]),
        multiplicity=int(meta["multiplicity"]),
    )
    return meta, record


def _sites(donors: list[int]) -> list[dict[str, Any]]:
    return [{"id": f"D{d}", "atoms": [d]} for d in donors]


def _input_scope(donors: list[int], shapes: list[str], search: dict[str, Any] | None = None):
    """Return the declaration; ``index_base: 0`` because the input-core fixtures are 0-based."""
    scope = _scope(
        index_base=0,
        seed=7,
        coordination={"metal_center": 0, "binding_sites": _sites(donors), "shapes": shapes},
        search={**_BUDGET, **(search or {})},
    )
    return scope


def test_input_shape_cn7_step_completes(tmp_path: Path) -> None:
    meta, driving = _fixture("ti7_salan")
    donors = list(meta["donors0"])
    assert len(donors) == 7
    native = FrozenDict(
        ConfgenModelV3.model_validate(_input_scope(donors, ["input"])).scientific_native()
    )
    out, _ = _run(native, driving, _write_fake(tmp_path), tmp_path)
    assert out.status.value == "completed", [str(d.message) for d in out.diagnostics]
    codes = [d.code for d in out.diagnostics]
    assert "confgen_input_shape_mismatch" not in codes
    assert len(out.structures) >= 1
    assert all(r.role == "conformer" for r in out.structures)
    assert all(tuple(r.atoms) == tuple(driving.atoms) for r in out.structures)
    assert all(dict(r.metadata)["search_target"] == "t00" for r in out.structures)
    assert all(dict(r.metadata)["search_actual_target"] == "t00" for r in out.structures)
    summary = _summary(out, tmp_path)
    assert summary["totals"]["passed"] == len(out.structures) >= 1
    assert summary["totals"]["targets"] == 1
    assert [t["id"] for t in summary["targets"]] == ["t00"]
    assert summary["search"]["targets_searched"] == ["t00"]
    assert summary["search"]["input_shape_fit"] is None
    assert summary["settings"]["arrangement"] == "keep"
    info = [d for d in out.diagnostics if d.code == "confgen_completed"]
    assert len(info) == 1


@pytest.mark.parametrize("shape", ["pentagonal_bipyramidal", "octahedral"])
def test_ideal_shape_rejects_cn7_declaration(shape: str) -> None:
    meta, _ = _fixture("ti7_salan")
    with pytest.raises(ValidationError, match="4 to 6 sites"):
        ConfgenModelV3.model_validate(_input_scope(list(meta["donors0"]), [shape]))


def test_input_shape_rejects_enumerate_at_declaration() -> None:
    meta, _ = _fixture("ti7_salan")
    scope = _input_scope(list(meta["donors0"]), ["input"], {"arrangement": "enumerate"})
    with pytest.raises(ValidationError, match="arrangement 'keep' only"):
        ConfgenModelV3.model_validate(scope)
    doc = {
        "schema": "confflow.workflow.v4",
        "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
        "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
        "steps": [
            {
                "id": "cg",
                "executor": "confgen",
                "bindings": {"structure": {"source": {"run": "structures"}}},
                "confgen": scope,
            }
        ],
    }
    assert not compile_workflow(doc).ok
