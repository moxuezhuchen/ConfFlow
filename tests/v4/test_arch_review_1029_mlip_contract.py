#!/usr/bin/env python3

"""Architecture review 2026-10-29, N3 and N4: MLIP keeps topology and result provenance.

N3  MLIP outputs carry the authoritative working graph (``topology_patch`` and
    ``working_topology``) inherited from their inputs, through the same propagation
    used by the standard result profile.  Bond changes observed after relaxation are
    diagnostics only, and the stretch check reads the authoritative input graph.
N4  The MLIP energy ``ScientificResult`` carries a full producer provenance, so the
    run-result manifest projects ``energies.electronic_hartree`` with a real source,
    before and after resume.

The scientific program is a fake runner; no model is loaded.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from confflow.application.v4_run import V4RunApplication, V4RunRequest, import_xyz
from confflow.domain import FrozenDict
from confflow.domain.topology import TopologyPatch
from confflow.execution import mlip_executor
from confflow.execution.mlip_analysis import stretch_check
from confflow.execution.mlip_executor import MODEL_ENV
from confflow.execution.process import NativeProcessSupervisor
from confflow.science.topology import resolve_working_adjacency
from confflow.workflow.v4.assembly import RunInputs
from tests.v4 import test_mlip_executor as h

#: Water with the O-H2 bond (one-based 1-3) declared deleted; 0-based adjacency.
DELETED_OH2_GRAPH = ((1,), (0,), ())
PATCH = TopologyPatch(delete_edges=((1, 3),))
WORKING = tuple(tuple(row) for row in DELETED_OH2_GRAPH)
MODES = ("sp", "relax", "sp_then_relax")
VARIANTS = ("patch", "working", "both")
PICK = {"lowest": 1}


def _input_record(variant: str) -> Any:
    record = h._records()[0]
    kwargs: dict[str, Any] = {}
    if variant in ("patch", "both"):
        kwargs["topology_patch"] = PATCH
    if variant in ("working", "both"):
        kwargs["working_topology"] = WORKING
    return replace(record, **kwargs)


def _rows(graph: Any) -> tuple[tuple[int, ...], ...] | None:
    return None if graph is None else tuple(tuple(int(v) for v in row) for row in graph)


def _graph(record: Any) -> list[list[int]]:
    return resolve_working_adjacency(record, [8, 1, 1], record.coordinates)


def _run_direct(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record: Any, **native: Any):
    h._install_runner(monkeypatch, tmp_path)
    result, _ = h._run(h._ctx(tmp_path, h._native(**native)), [record], cores=1)
    assert result.status.value == "completed", result.diagnostics
    return result


# ---------------------------------------------------------------------------
# N3: authoritative topology survives the MLIP executor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("mode", MODES)
def test_output_keeps_authoritative_topology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, variant: str
) -> None:
    record = _input_record(variant)
    native: dict[str, Any] = {"mode": mode}
    if mode == "sp_then_relax":
        native["pick"] = PICK
    result = _run_direct(tmp_path, monkeypatch, record, **native)
    output = result.structures[0]

    assert output.topology_patch == (PATCH if variant in ("patch", "both") else None)
    assert _rows(output.working_topology) == WORKING
    assert _rows(_graph(output)) == DELETED_OH2_GRAPH


def test_observed_bond_change_after_relax_is_diagnostic_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _input_record("both")
    # Pull H2 far away so the observed geometry no longer bonds it to O.
    far = {"s00001": [[0.0, 0.0, 0.0], [0.96, 0.0, 0.0], [-3.0, 0.0, 0.0]]}
    h._install_runner(monkeypatch, tmp_path, relax_coords=far)
    result, _ = h._run(h._ctx(tmp_path, h._native(mode="relax")), [record], cores=1)
    assert result.status.value == "completed", result.diagnostics
    output = result.structures[0]

    assert _rows(output.working_topology) == WORKING
    assert output.topology_patch == PATCH
    assert _rows(_graph(output)) == DELETED_OH2_GRAPH
    assert output.metadata["mlip_graph_changed"] is True


def test_stretch_check_ignores_bond_removed_by_authoritative_patch() -> None:
    record = _input_record("patch")
    # H2 flies away: the only bond it had was the deleted O-H2 bond.
    after = [[0.0, 0.0, 0.0], [0.96, 0.0, 0.0], [-3.0, 0.0, 0.0]]
    ratio, pair, changed = stretch_check(record, after, ())

    assert pair == (0, 1)
    assert ratio == pytest.approx(1.0)
    assert changed is True


def test_stretch_check_counts_declared_bond_that_perception_misses() -> None:
    # H1-H2 (1.52 A apart) is outside perception; the declared graph bonds only that pair.
    # O-H2 is not declared, so perception's O-H2 bond must not drive the stretch report.
    record = replace(h._records()[0], working_topology=((), (2,), (1,)))
    after = [[0.0, 0.0, 0.0], [0.96, 0.0, 0.0], [-2.0, 0.0, 0.0]]
    ratio, pair, _ = stretch_check(record, after, ())

    assert pair == (1, 2)
    input_h1_h2 = math.dist((0.96, 0.0, 0.0), (-0.24, 0.93, 0.0))
    assert ratio == pytest.approx(math.dist((0.96, 0.0, 0.0), (-2.0, 0.0, 0.0)) / input_h1_h2)


def _mlip_document(model: Path, mode: str, executable: str) -> dict[str, Any]:
    native: dict[str, Any] = {"model": "uma-s-1p2p1", "mode": mode}
    if mode == "sp_then_relax":
        native["pick"] = PICK
    return {
        "schema": "confflow.workflow.v4",
        "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
        "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
        "steps": [
            {
                "id": "uma",
                "executor": "mlip",
                "bindings": {"structure": {"source": {"run": "structures"}}},
                "mlip": native,
                "execution": {
                    "binding_id": "uma",
                    "executable": executable,
                    "env": {MODEL_ENV: str(model)},
                },
            }
        ],
    }


_WATER_XYZ = "3\nwater\nO 0 0 0\nH 0.96 0 0\nH -0.24 0.93 0\n"

#: Fake runner: one sp row per structure, one relax row per structure (unmoved geometry),
#: the fairchem version the real runner would measure.
_FAKE_RUNNER = """\
import json
from pathlib import Path
job = json.loads(Path("job.json").read_text(encoding="utf-8"))
rows = []
for item in job["structures"]:
    rows.append({"key": item["key"], "stage": "sp", "energy_eh": -5.0,
                 "n_evals": 1, "wall_s": 0.1})
    if job["mode"] != "sp":
        rows.append({"key": item["key"], "stage": "relax", "energy_eh": -5.01,
                     "coords": item["coords"], "converged": True, "n_steps": 3,
                     "n_evals": 4, "wall_s": 0.2, "optimizer": job["optimizer"],
                     "fix_max_dev": 0.0, "error": None})
Path("mlip_results.jsonl").write_text(
    "".join(json.dumps(row) + "\\n" for row in rows), encoding="utf-8")
Path("mlip_done.json").write_text(json.dumps(
    {"model_sha256": "fake", "wall_s": 0.1, "fairchem_version": "9.9.9-test"}),
    encoding="utf-8")
"""


def _install_workflow_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runner = tmp_path / "fake_mlip_runner.py"
    runner.write_text(_FAKE_RUNNER, encoding="utf-8")
    monkeypatch.setattr(mlip_executor, "RUNNER_PATH", runner)


def _run_workflow(run_root: Path, document: dict[str, Any]) -> Any:
    return V4RunApplication(supervisor=NativeProcessSupervisor()).run(
        V4RunRequest(
            workflow_document=document,
            run_inputs=RunInputs(structures=FrozenDict({"structures": import_xyz(_WATER_XYZ)})),
            import_sources=FrozenDict({"structures": _WATER_XYZ}),
            run_root=str(run_root),
            executables=FrozenDict(),
        )
    )


@pytest.mark.parametrize("mode", MODES)
def test_workflow_keeps_declared_topology_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    _install_workflow_runner(monkeypatch, tmp_path)
    model = tmp_path / "model.pt"
    model.write_text("-5\n", encoding="utf-8")
    document = _mlip_document(model, mode, sys.executable)
    document["inputs"]["structures"]["topology"] = {"delete": [[1, 3]]}

    report = _run_workflow(tmp_path / "run", document)

    assert report.status == "completed", report
    output = report.step_results[0].structures[0]
    assert output.topology_patch is not None
    assert output.topology_patch.delete_edges == ((1, 3),)
    assert _rows(_graph(output)) == DELETED_OH2_GRAPH


# ---------------------------------------------------------------------------
# N4: the energy result carries real producer provenance
# ---------------------------------------------------------------------------


def test_energy_result_carries_program_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run_direct(tmp_path, monkeypatch, h._records()[0], mode="sp")
    energy = result.results[0]

    assert energy.kind == "energy"
    assert energy.provenance is not None
    assert energy.provenance.program == "fairchem"
    assert energy.provenance.method == "uma-s-1p2p1"
    assert energy.provenance.step_id == "uma"
    assert energy.provenance.work_item_id


def _energy_entries(run_root: Path) -> list[dict[str, Any]]:
    manifest = json.loads((run_root / "run_result.json").read_text(encoding="utf-8"))
    return [entry for entry in manifest["results"] if entry.get("kind") == "energy"]


def test_manifest_projects_inline_electronic_energy_with_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_workflow_runner(monkeypatch, tmp_path)
    model = tmp_path / "model.pt"
    model.write_text("-5\n", encoding="utf-8")
    run_root = tmp_path / "run"
    document = _mlip_document(model, "sp", sys.executable)

    report = _run_workflow(run_root, document)

    assert report.status == "completed", report
    entries = _energy_entries(run_root)
    assert len(entries) == 1
    energies = entries[0]["energies"]
    assert energies["electronic_hartree"] == pytest.approx(-5.0)
    assert energies["source"]["program"] == "fairchem"
    assert energies["source"]["method"] == "uma-s-1p2p1"


def test_manifest_energy_projection_survives_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_workflow_runner(monkeypatch, tmp_path)
    model = tmp_path / "model.pt"
    model.write_text("-5\n", encoding="utf-8")
    run_root = tmp_path / "run"
    document = _mlip_document(model, "sp", sys.executable)
    assert _run_workflow(run_root, document).status == "completed"
    first = _energy_entries(run_root)

    resumed = _run_workflow(run_root, document)

    assert resumed.status == "completed", resumed
    assert _energy_entries(run_root) == first
    assert first[0]["energies"]["electronic_hartree"] == pytest.approx(-5.0)
