#!/usr/bin/env python3

"""MlipExecutor: job contract, runner supervision, result assembly and diagnostics.

A fake runner (written from each test into ``tmp_path``) replaces the real UMA runner, so no
model is loaded.  Its behaviour is a literal dict embedded in the script text.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from confflow.domain._immutable import FrozenDict
from confflow.domain.completion import WorkItemStatus
from confflow.domain.resources import ResourceRequest
from confflow.domain.structure import StructureRecord, StructureSet
from confflow.domain.work_item import (
    WorkItem,
    WorkItemInputs,
    make_work_item_id,
)
from confflow.execution import mlip_executor
from confflow.execution.contracts import ExecutionBinding
from confflow.execution.energy_filter import parse_energy_params, select_by_energy
from confflow.execution.mlip_executor import MlipExecutor
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.work_item_executor import ItemExecutionContext

HARTREE_TO_KCAL = 627.5094740631

WATER = (
    ("O", "H", "H"),
    ((0.0, 0.0, 0.0), (0.96, 0.0, 0.0), (-0.24, 0.93, 0.0)),
)
ENV = {"CONFFLOW_MLIP_MODEL_PATH": "/models/uma.pt", "CONFFLOW_MLIP_THREADS": "4"}

FAKE_BODY = """
import json, sys, time
job = json.load(open("job.json"))
with open("mlip_runner.log", "w") as log:
    log.write("fake runner ran\\n")
if BEH.get("sleep"):
    time.sleep(BEH["sleep"])
if BEH.get("exit"):
    sys.stderr.write(BEH.get("stderr", "boom"))
    sys.exit(BEH["exit"])
rows = []
picked = BEH.get("picked")
for index, struct in enumerate(job["structures"]):
    key = struct["key"]
    if key in BEH.get("skip", []):
        continue
    energy = BEH.get("energies", {}).get(key, -76.0 + 0.001 * index)
    if key in BEH.get("sp_error", []):
        rows.append({"key": key, "stage": "sp", "error": "sp failed"})
    else:
        rows.append({"key": key, "stage": "sp", "energy_eh": energy, "n_evals": 1, "wall_s": 0.1})
    if job["mode"] == "sp":
        continue
    if picked is not None and key not in picked:
        continue
    if key in BEH.get("relax_error", []):
        rows.append({"key": key, "stage": "relax", "error": "relax failed"})
        continue
    coords = BEH.get("relax_coords", {}).get(key, struct["coords"])
    if key in BEH.get("energy_null", []):
        energy = None
    rows.append({
        "key": key, "stage": "relax",
        "energy_eh": None if energy is None else energy - 0.01,
        "coords": coords,
        "converged": key not in BEH.get("unconverged", []),
        "n_steps": 10, "n_evals": 7, "wall_s": 1.5, "optimizer": job["optimizer"],
        "fix_max_dev": 0.0, "error": None,
    })
if BEH.get("bad_json"):
    rows_text = "not json\\n"
else:
    rows_text = "".join(json.dumps(row) + "\\n" for row in rows)
with open("mlip_results.jsonl", "a") as handle:
    handle.write(rows_text)
if BEH.get("done", True):
    with open("mlip_done.json", "w") as handle:
        json.dump({"model_sha256": "abc123", "wall_s": 3.5}, handle)
"""


def _install_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **behaviour: Any) -> Path:
    """Write the fake runner for *behaviour* and point the executor at it."""
    script = tmp_path / "fake_runner.py"
    script.write_text(
        "import json\nBEH = json.loads(" + repr(json.dumps(behaviour)) + ")\n" + FAKE_BODY
    )
    monkeypatch.setattr(mlip_executor, "RUNNER_PATH", script)
    return script


def _records(atoms_coords: Any = WATER) -> list[StructureRecord]:
    atoms, coords = atoms_coords
    out = []
    for name, shift, flips in (("w1", 0.0, "-+"), ("w2", 0.4, "+-"), ("w3", 0.8, None)):
        moved = tuple((x + shift, y, z) for x, y, z in coords)
        metadata = {} if flips is None else {"search_state_flips": flips}
        out.append(
            StructureRecord(
                id=name,
                atoms=tuple(atoms),
                coordinates=moved,
                charge=0,
                multiplicity=1,
                metadata=FrozenDict(metadata),
            )
        )
    return out


def _native(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "model": "uma-s-1p2p1",
        "task": "omol",
        "mode": "sp",
        "index_base": 1,
        "relax": {"optimizer": "lbfgs", "fmax": 0.02, "max_steps": 600, "fix_distances": []},
        "pick": None,
        "reject_bond_stretch": None,
    }
    base.update(over)
    return base


def _ctx(
    tmp_path: Path,
    native: dict[str, Any],
    *,
    env: dict[str, str] | None = ENV,
    walltime: int | None = None,
    executable: str | None = None,
    cores: int | None = 12,
) -> ItemExecutionContext:
    binding = ExecutionBinding(
        binding_id="mlip-test",
        executable=executable or sys.executable,
        walltime_seconds=walltime,
    )
    return ItemExecutionContext(
        step_id="uma",
        scientific=SimpleNamespace(native=native, overrides={}),
        scientific_defaults=SimpleNamespace(charge=0, multiplicity=1),
        adapter=None,
        profile=None,
        checks=(),
        recovery=None,
        execution_binding=binding,
        run_root=str(tmp_path / "run"),
        supervisor=NativeProcessSupervisor(),
        native_env=None if env is None else FrozenDict(env),
        poll_interval_seconds=0.01,
        attempt=0,
    )


def _item(records: list[StructureRecord], cores: int | None = 12) -> WorkItem:
    return WorkItem(
        id=make_work_item_id("uma:whole"),
        logical_key="uma:whole",
        step_id="uma",
        named_inputs=WorkItemInputs(
            structures=FrozenDict({"structure": StructureSet(tuple(records))})
        ),
        resources=ResourceRequest(cores_per_item=cores, memory_per_item_bytes=2**30),
        semantic_digest="sha256:" + "a" * 64,
    )


def _run(
    ctx: ItemExecutionContext, records: list[StructureRecord], cores: int | None = 12, **kw: Any
) -> Any:
    item = _item(records, cores)
    return MlipExecutor().execute(item, ctx, **kw), item


def _job(ctx: ItemExecutionContext, records: list[StructureRecord]) -> dict[str, Any]:
    item = _item(records)
    path = Path(ctx.attempt_dir(item)) / "job.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _message(result: Any) -> str:
    assert result.diagnostics, "failed result carries no diagnostic"
    return str(result.diagnostics[0].message)


def _plain(value: Any) -> Any:
    """Thaw frozen details into plain dicts and lists for comparison."""
    if isinstance(value, FrozenDict) or isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _diag(result: Any, code: str) -> Any:
    matches = [d for d in result.diagnostics if d.code == code]
    assert matches, f"no {code} diagnostic in {[d.code for d in result.diagnostics]}"
    return SimpleNamespace(code=code, details=_plain(matches[0].details))


def test_sp_mode_outputs_every_structure_and_energies_feed_energy_filter(monkeypatch, tmp_path):
    _install_runner(
        monkeypatch, tmp_path, energies={"s00001": -76.0, "s00002": -76.02, "s00003": -75.99}
    )
    records = _records()
    ctx = _ctx(tmp_path, _native(mode="sp"))
    result, _ = _run(ctx, records)
    assert result.status is WorkItemStatus.COMPLETED
    ids = sorted(record.id for record in result.structures)
    assert ids == ["w1:mlip-sp", "w2:mlip-sp", "w3:mlip-sp"]
    for record in result.structures:
        assert record.parent_ids == (record.id.split(":")[0],)
        assert record.metadata["mlip_stage"] == "sp"
        assert record.metadata["mlip_model"] == "uma-s-1p2p1"
        assert record.metadata["energy_eh"] == record.metadata["mlip_energy_eh"]
        assert record.metadata["mlip_graph_changed"] is False
        assert record.metadata["mlip_max_bond_stretch"] is None
    energies = {r.subject_structure_id: r.value for r in result.results}
    assert energies["w2:mlip-sp"] == pytest.approx(-76.02)
    params = parse_energy_params({"energy_window_kcal": 1.0})
    kept, notes = select_by_energy(list(result.structures), result.results, params)
    assert [record.id for record in kept] == ["w2:mlip-sp", "w1:mlip-sp"] or [
        record.id for record in kept
    ] == ["w2:mlip-sp"]
    assert notes is not None
    completed = _diag(result, "mlip_completed")
    assert completed.details["sp_count"] == 3
    assert completed.details["relaxed_count"] == 0


def test_sp_then_relax_outputs_only_picked_relaxed_structures(monkeypatch, tmp_path):
    _install_runner(
        monkeypatch,
        tmp_path,
        picked=["s00002"],
        relax_coords={"s00002": [[0.0, 0.0, 0.0], [0.97, 0.0, 0.0], [-0.24, 0.94, 0.0]]},
    )
    records = _records()
    ctx = _ctx(tmp_path, _native(mode="sp_then_relax", pick={"lowest": 1, "per_label": None}))
    result, _ = _run(ctx, records)
    assert result.status is WorkItemStatus.COMPLETED
    assert [record.id for record in result.structures] == ["w2:mlip-relax"]
    out = list(result.structures)[0]
    assert out.coordinates[1][0] == pytest.approx(0.97)
    assert out.metadata["mlip_stage"] == "relax"
    assert out.metadata["mlip_optimizer"] == "lbfgs"
    assert out.metadata["mlip_converged"] is True
    assert out.metadata["mlip_n_evals"] == 7
    assert out.metadata["mlip_sp_energy_eh"] == pytest.approx(-76.0 + 0.001 * 1)
    assert out.metadata["mlip_energy_eh"] == pytest.approx(-76.0 + 0.001 * 1 - 0.01)
    completed = _diag(result, "mlip_completed")
    assert completed.details["sp_count"] == 3
    assert completed.details["relaxed_count"] == 1
    assert completed.details["mean_n_evals"] == pytest.approx(7.0)
    assert completed.details["mean_wall_s"] == pytest.approx(1.5)
    assert completed.details["model_sha256"] == "abc123"
    assert completed.details["wall_s"] == pytest.approx(3.5)


def test_sp_then_relax_requires_pick(tmp_path):
    result, _ = _run(_ctx(tmp_path, _native(mode="sp_then_relax", pick=None)), _records())
    assert result.status is WorkItemStatus.FAILED
    assert "pick" in _message(result)


def test_error_rows_are_dropped_and_listed(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path, sp_error=["s00001"])
    ctx = _ctx(tmp_path, _native(mode="sp"))
    result, _ = _run(ctx, _records())
    assert [record.id for record in result.structures] == ["w2:mlip-sp", "w3:mlip-sp"]
    completed = _diag(result, "mlip_completed")
    assert completed.details["dropped_error"] == 1
    assert completed.details["error_rows"][0]["error"] == "sp failed"
    assert _diag(result, "mlip_dropped").details["error"] == ["w1"]


def test_relax_error_and_missing_row_are_dropped(monkeypatch, tmp_path):
    _install_runner(
        monkeypatch, tmp_path, picked=["s00001", "s00002"], relax_error=["s00001"], skip=["s00003"]
    )
    ctx = _ctx(tmp_path, _native(mode="sp_then_relax", pick={"lowest": 2, "per_label": None}))
    result, _ = _run(ctx, _records())
    assert [record.id for record in result.structures] == ["w2:mlip-relax"]
    dropped = _diag(result, "mlip_dropped").details["error"]
    assert dropped == ["w1"]


def test_sp_mode_missing_row_is_dropped_with_no_result_message(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path, skip=["s00003"])
    result, _ = _run(_ctx(tmp_path, _native(mode="sp")), _records())
    assert [record.id for record in result.structures] == ["w1:mlip-sp", "w2:mlip-sp"]
    rows = _diag(result, "mlip_completed").details["error_rows"]
    assert rows == [{"id": "w3", "stage": "sp", "error": "no result row"}]


def test_null_energy_and_bad_coordinates_are_dropped(monkeypatch, tmp_path):
    _install_runner(
        monkeypatch,
        tmp_path,
        picked=["s00001", "s00002"],
        energy_null=["s00001"],
        relax_coords={"s00002": [[0.0, 0.0, 0.0]]},
    )
    ctx = _ctx(tmp_path, _native(mode="relax"))
    result, _ = _run(ctx, _records())
    assert result.structures == StructureSet() or len(result.structures) == 0
    details = _diag(result, "mlip_completed").details["error_rows"]
    messages = {row["id"]: row["error"] for row in details}
    assert "no electronic energy" in messages["w1"]
    assert "atom count" in messages["w2"]


def test_reject_bond_stretch_drops_stretched_structure(monkeypatch, tmp_path):
    stretched = [[0.0, 0.0, 0.0], [1.6, 0.0, 0.0], [-0.24, 0.93, 0.0]]
    _install_runner(monkeypatch, tmp_path, relax_coords={"s00001": stretched})
    ctx = _ctx(tmp_path, _native(mode="relax", reject_bond_stretch=1.25))
    result, _ = _run(ctx, _records())
    assert "w1:mlip-relax" not in [record.id for record in result.structures]
    assert _diag(result, "mlip_dropped").details["stretch"] == ["w1"]
    assert _diag(result, "mlip_completed").details["dropped_stretch"] == 1


def test_stretch_without_reject_is_flagged_and_graph_change_warned(monkeypatch, tmp_path):
    stretched = [[0.0, 0.0, 0.0], [1.6, 0.0, 0.0], [-0.24, 0.93, 0.0]]
    _install_runner(
        monkeypatch, tmp_path, relax_coords={"s00001": stretched}, unconverged=["s00002"]
    )
    ctx = _ctx(tmp_path, _native(mode="relax"))
    result, _ = _run(ctx, _records())
    out = {record.id: record for record in result.structures}["w1:mlip-relax"]
    assert out.metadata["mlip_max_bond_stretch"] == pytest.approx(1.6 / 0.96, rel=1e-3)
    assert out.metadata["mlip_stretched_bond"] == "1-2"
    assert out.metadata["mlip_graph_changed"] is True
    assert _diag(result, "mlip_bond_stretched").details["structures"] == ["w1"]
    assert _diag(result, "mlip_graph_changed").details["structures"] == ["w1"]
    assert _diag(result, "mlip_not_converged").details["structures"] == ["w2"]


def test_fixed_pair_is_excluded_from_bond_check(monkeypatch, tmp_path):
    stretched = [[0.0, 0.0, 0.0], [1.6, 0.0, 0.0], [-0.24, 0.93, 0.0]]
    _install_runner(monkeypatch, tmp_path, relax_coords={"s00001": stretched})
    native = _native(mode="relax", relax={"fix_distances": [[1, 2]]}, reject_bond_stretch=1.25)
    ctx = _ctx(tmp_path, native)
    result, _ = _run(ctx, _records())
    kept = {record.id: record for record in result.structures}
    assert "w1:mlip-relax" in kept
    assert kept["w1:mlip-relax"].metadata["mlip_max_bond_stretch"] == pytest.approx(1.0)
    assert kept["w1:mlip-relax"].metadata["mlip_graph_changed"] is False


def test_fix_deviation_is_recorded(monkeypatch, tmp_path):
    moved = [[0.0, 0.0, 0.0], [0.90, 0.0, 0.0], [-0.24, 0.93, 0.0]]
    _install_runner(monkeypatch, tmp_path, relax_coords={"s00001": moved})
    native = _native(mode="relax", relax={"fix_distances": [[1, 2]]})
    result, _ = _run(_ctx(tmp_path, native), _records())
    out = {record.id: record for record in result.structures}["w1:mlip-relax"]
    assert out.metadata["mlip_fix_max_dev"] == pytest.approx(0.06, abs=1e-9)


def test_missing_model_path_names_the_variable(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path)
    result, _ = _run(_ctx(tmp_path, _native(), env={}), _records())
    assert result.status is WorkItemStatus.FAILED
    assert "CONFFLOW_MLIP_MODEL_PATH" in _message(result)


def test_threads_must_be_an_integer(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path)
    env = {**ENV, "CONFFLOW_MLIP_THREADS": "four"}
    result, _ = _run(_ctx(tmp_path, _native(), env=env), _records())
    assert "CONFFLOW_MLIP_THREADS" in _message(result)


def test_threads_must_be_positive(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path)
    env = {**ENV, "CONFFLOW_MLIP_THREADS": "0"}
    result, _ = _run(_ctx(tmp_path, _native(), env=env), _records())
    assert "CONFFLOW_MLIP_THREADS" in _message(result)


def test_fix_distances_out_of_range_or_repeated_fail(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path)
    out_of_range = _native(relax={"fix_distances": [[1, 4]]})
    result, _ = _run(_ctx(tmp_path, out_of_range), _records())
    assert result.status is WorkItemStatus.FAILED
    assert "out of range" in _message(result)
    repeated = _native(relax={"fix_distances": [[2, 2]]})
    result, _ = _run(_ctx(tmp_path, repeated), _records())
    assert "repeats one atom" in _message(result)


def test_index_base_conversion_lands_in_job_json(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path)
    one = _ctx(
        tmp_path / "one", _native(mode="sp", index_base=1, relax={"fix_distances": [[1, 3]]})
    )
    _run(one, _records())
    assert _job(one, _records())["fix_pairs"] == [[0, 2]]
    zero = _ctx(
        tmp_path / "zero", _native(mode="sp", index_base=0, relax={"fix_distances": [[0, 2]]})
    )
    _run(zero, _records())
    assert _job(zero, _records())["fix_pairs"] == [[0, 2]]
    assert _job(zero, _records())["workers"] == 3
    assert _job(zero, _records())["threads"] == 4
    assert _job(zero, _records())["model_path"] == "/models/uma.pt"
    assert _job(zero, _records())["refs_path"] is None
    assert _job(zero, _records())["device"] == "cpu"


def test_job_structures_have_stable_keys_and_labels(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path)
    env = {**ENV, "CONFFLOW_MLIP_REFS_PATH": "/opt/refs.yaml", "CONFFLOW_MLIP_DEVICE": "cuda"}
    ctx = _ctx(tmp_path, _native(mode="sp"), env=env, cores=2)
    _run(ctx, _records(), cores=2)
    job = _job(ctx, _records())
    assert [s["key"] for s in job["structures"]] == ["s00001", "s00002", "s00003"]
    assert [s["label"] for s in job["structures"]] == ["-+", "+-", None]
    assert job["refs_path"] == "/opt/refs.yaml"
    assert job["device"] == "cuda"
    assert job["workers"] == 1
    assert job["charge"] == 0 and job["multiplicity"] == 1


def test_unknown_mode_and_bad_native_values_fail(tmp_path):
    cases = [
        _native(mode="md"),
        _native(model=""),
        _native(index_base=2),
        _native(relax={"optimizer": "bfgs"}),
        _native(relax={"fmax": -1}),
        _native(relax={"max_steps": 0}),
        _native(reject_bond_stretch=0.9),
        _native(pick={"lowest": -1}),
        _native(relax={"fix_distances": [[1]]}),
    ]
    for native in cases:
        result, _ = _run(_ctx(tmp_path, native), _records())
        assert result.status is WorkItemStatus.FAILED, native


def test_structure_input_and_context_errors_fail(tmp_path):
    ctx = _ctx(tmp_path, _native())
    empty = WorkItem(
        id=make_work_item_id("uma:whole"),
        logical_key="uma:whole",
        step_id="uma",
        named_inputs=WorkItemInputs(structures=FrozenDict({"structure": StructureSet(())})),
        resources=ResourceRequest(cores_per_item=12, memory_per_item_bytes=2**30),
        semantic_digest="sha256:" + "a" * 64,
    )
    assert MlipExecutor().execute(empty, ctx).status is WorkItemStatus.FAILED
    no_root = dataclasses.replace(ctx, run_root="")
    assert "run root" in _message(MlipExecutor().execute(_item(_records()), no_root))
    no_sup = dataclasses.replace(ctx, supervisor=None)
    assert "supervisor" in _message(MlipExecutor().execute(_item(_records()), no_sup))


def test_missing_runner_source_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(mlip_executor, "RUNNER_PATH", tmp_path / "absent.py")
    result, _ = _run(_ctx(tmp_path, _native()), _records())
    assert "runner source is missing" in _message(result)


def test_interpreter_must_exist(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path, _native(), executable=str(tmp_path / "no-python"))
    result, _ = _run(ctx, _records())
    assert "interpreter not found" in _message(result)


def test_default_interpreter_resolves_on_path(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path, _native(), executable="")
    ctx = dataclasses.replace(ctx, execution_binding=None)
    monkeypatch.setattr(mlip_executor.shutil, "which", lambda name: sys.executable)
    result, _ = _run(ctx, _records())
    assert result.status is WorkItemStatus.COMPLETED


def test_default_interpreter_missing_on_path_fails(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path)
    ctx = dataclasses.replace(_ctx(tmp_path, _native()), execution_binding=None)
    monkeypatch.setattr(mlip_executor.shutil, "which", lambda name: None)
    result, _ = _run(ctx, _records())
    assert "not found on PATH" in _message(result)


def test_nonzero_exit_reports_stderr_tail(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path, exit=3, stderr="CUDA out of memory")
    result, _ = _run(_ctx(tmp_path, _native()), _records())
    assert result.status is WorkItemStatus.FAILED
    assert "exited with code 3" in _message(result)
    assert "CUDA out of memory" in _message(result)


def test_walltime_overrun_fails(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path, sleep=30)
    result, _ = _run(_ctx(tmp_path, _native(), walltime=1), _records())
    assert result.status is WorkItemStatus.FAILED
    assert "walltime" in _message(result)


def test_cancel_before_launch_is_cancelled(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path)
    result, _ = _run(_ctx(tmp_path, _native()), _records(), should_cancel=lambda: True)
    assert result.status is WorkItemStatus.CANCELLED


def test_cancel_during_run_is_cancelled(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path, sleep=30)
    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    result, _ = _run(_ctx(tmp_path, _native()), _records(), should_cancel=cancel)
    assert result.status is WorkItemStatus.CANCELLED


def test_bad_results_jsonl_fails(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path, bad_json=True)
    result, _ = _run(_ctx(tmp_path, _native()), _records())
    assert result.status is WorkItemStatus.FAILED
    assert "not JSON" in _message(result)


def test_calibration_reports_spearman_and_kcal_gap(monkeypatch, tmp_path):
    _install_runner(
        monkeypatch,
        tmp_path,
        energies={"s00001": -76.00, "s00002": -76.02, "s00003": -75.99},
    )
    records = [
        dataclasses.replace(
            record,
            metadata=FrozenDict({**record.metadata.thaw(), "energy_eh": energy}),
        )
        for record, energy in zip(_records(), (-76.0, -75.9, -76.1))
    ]
    result, _ = _run(_ctx(tmp_path, _native(mode="sp")), records)
    calibration = _diag(result, "mlip_calibration").details
    assert calibration["basis"] == "sp_all_inputs"
    assert calibration["n"] == 3
    assert calibration["spearman"] == pytest.approx(-1.0)
    assert calibration["input_lowest"] == "w3"
    expected_kcal = (-75.99 - (-76.02)) * HARTREE_TO_KCAL
    assert calibration["kcal_above_mlip_lowest"] == pytest.approx(expected_kcal)


def test_artifacts_carry_checksums_and_run_relative_locators(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path)
    ctx = _ctx(tmp_path, _native(mode="sp"))
    result, item = _run(ctx, _records())
    roles = {ref.role: ref for ref in result.artifacts}
    assert set(roles) == {"mlip_job", "mlip_results", "mlip_done", "mlip_log", "mlip_structures"}
    for ref in roles.values():
        path = Path(ctx.run_root) / ref.locator.path
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert ref.checksum == "sha256:" + digest
    xyz = (Path(ctx.attempt_dir(item)) / "mlip_structures.xyz").read_text(encoding="utf-8")
    assert xyz.count("key=s0000") == 3
    assert "energy_eh=" in xyz and "max_bond_stretch=None" in xyz


def test_done_file_absent_is_tolerated(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path, done=False)
    result, _ = _run(_ctx(tmp_path, _native(mode="sp")), _records())
    assert result.status is WorkItemStatus.COMPLETED
    assert _diag(result, "mlip_completed").details["model_sha256"] is None


def test_unexpected_exception_fails_closed(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(MlipExecutor, "_run", boom)
    result, _ = _run(_ctx(tmp_path, _native()), _records())
    assert result.status is WorkItemStatus.FAILED
    assert "internal failure" in _message(result)


def test_calibration_uses_sp_energies_of_all_inputs_with_sp_rows(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path, picked=["s00002"], skip=["s00003"])
    records = _with_input_energy(_records(), (-76.0, -75.9, -76.1))
    result, _ = _run(
        _ctx(tmp_path, _native(mode="sp_then_relax", pick={"lowest": 1, "per_label": None})),
        records,
    )
    calibration = _diag(result, "mlip_calibration").details
    assert calibration["basis"] == "sp_all_inputs"
    assert calibration["n"] == 2


def test_calibration_falls_back_to_relaxed_outputs_in_relax_mode(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path)
    records = _with_input_energy(_records(), (-76.0, -75.9, -76.1))
    result, _ = _run(_ctx(tmp_path, _native(mode="relax")), records)
    calibration = _diag(result, "mlip_calibration").details
    assert calibration["basis"] == "relaxed_outputs"
    assert calibration["n"] == 3


def test_output_metadata_keeps_input_energy_and_drops_rel_kcal(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path)
    records = _with_input_energy(_records(), (-76.0, -75.9, -76.1))
    records = [
        dataclasses.replace(
            record, metadata=FrozenDict({**record.metadata.thaw(), "rel_kcal": 1.5})
        )
        for record in records
    ]
    result, _ = _run(_ctx(tmp_path, _native(mode="sp")), records)
    for out in result.structures:
        assert "rel_kcal" not in out.metadata
    by_id = {out.id: out for out in result.structures}
    assert by_id["w1:mlip-sp"].metadata["mlip_input_energy_eh"] == pytest.approx(-76.0)
    assert by_id["w1:mlip-sp"].metadata["search_state_flips"] == "-+"


def _with_input_energy(
    records: list[StructureRecord], energies: tuple[float, ...]
) -> list[StructureRecord]:
    return [
        dataclasses.replace(
            record, metadata=FrozenDict({**record.metadata.thaw(), "energy_eh": energy})
        )
        for record, energy in zip(records, energies)
    ]


def test_fix_deviation_is_measured_against_the_target(monkeypatch, tmp_path):
    # O-H1 is 0.96 A on input; the target is 1.10 A and the runner ends at 1.13 A.
    moved = [[0.0, 0.0, 0.0], [1.13, 0.0, 0.0], [-0.24, 0.93, 0.0]]
    _install_runner(monkeypatch, tmp_path, relax_coords={"s00001": moved})
    native = _native(mode="relax", relax={"fix_distances": [[1, 2, 1.10]]})
    ctx = _ctx(tmp_path, native)
    result, _ = _run(ctx, _records())
    out = {record.id: record for record in result.structures}["w1:mlip-relax"]
    assert out.metadata["mlip_fix_max_dev"] == pytest.approx(0.03, abs=1e-9)
    assert _job(ctx, _records())["fix_pairs"] == [[0, 1, 1.1]]


def test_fix_target_entries_convert_index_base_in_job_json(monkeypatch, tmp_path):
    _install_runner(monkeypatch, tmp_path)
    one = _ctx(
        tmp_path / "one", _native(mode="relax", index_base=1, relax={"fix_distances": [[1, 3, 2]]})
    )
    _run(one, _records())
    assert _job(one, _records())["fix_pairs"] == [[0, 2, 2.0]]
    mixed = _ctx(
        tmp_path / "mixed",
        _native(mode="relax", index_base=0, relax={"fix_distances": [[0, 2], [1, 2, 1.2]]}),
    )
    _run(mixed, _records())
    assert _job(mixed, _records())["fix_pairs"] == [[0, 2], [1, 2, 1.2]]


@pytest.mark.parametrize("target", [0, -1.0, True, "1.1"])
def test_bad_fix_target_fails_the_step(monkeypatch, tmp_path, target):
    _install_runner(monkeypatch, tmp_path)
    native = _native(relax={"fix_distances": [[1, 2, target]]})
    result, _ = _run(_ctx(tmp_path, native), _records())
    assert result.status is WorkItemStatus.FAILED
    assert "fix_distances" in _message(result)
