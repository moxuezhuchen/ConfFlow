#!/usr/bin/env python3

"""Confgen search step tests: schema, executor, binding end to end."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from confflow.domain import FrozenDict, StructureSet
from confflow.domain.resources import ResourceRequest
from confflow.domain.result import ResultSet, ScientificResult, make_result_id
from confflow.domain.structure import StructureRecord
from confflow.domain.work_item import WorkItem, WorkItemInputs, make_work_item_id
from confflow.execution import confgen_search_run as search_run
from confflow.execution.confgen_executor import ConfgenExecutor
from confflow.execution.contracts import ExecutionBinding
from confflow.execution.native import CancelOutcome, NativeExecutionResult
from confflow.execution.output_identity import CONFORMER_MEMBER_METADATA_KEY
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.quota import QuotaCancelled, QuotaError
from confflow.execution.work_item_executor import ItemExecutionContext, WorkItemExecutor
from confflow.workflow.v4.compiler import compile_workflow
from confflow.workflow.v4.confgen_schema import REMOVED_ENGINES_MESSAGE, ConfgenModelV3
from confflow.workflow.v4.validation import _confgen_seed_requirement
from tests.v4._dg_search_helpers import _embed, _toy, _write_fake

_SEARCH = {"starts": 2, "embed_timeout_seconds": 20, "max_cycles": 100, "bond_scale": 1.25}
_SITES = [{"id": f"s{i}", "atoms": [d]} for i, d in enumerate((2, 3, 4, 5))]

# Byte-exact wire pin: sha256 of the canonical minimal v4 scope wire
# (schema_version 4, seed 1; search absent means all defaults).
_WIRE_V4_DIGEST = "6a8663a8615b096fc0c68d462d49318dab8cd9e784a3d5782bbf4df2d089a20d"


def _scope(**over: Any) -> dict[str, Any]:
    scope: dict[str, Any] = {
        "schema_version": 4,
        "coordination": {"metal_center": 1, "binding_sites": _SITES, "shapes": ["square_planar"]},
        "seed": 7,
        "search": dict(_SEARCH),
    }
    scope.update(over)
    return scope


def _native(**over: Any) -> FrozenDict:
    return FrozenDict(ConfgenModelV3.model_validate(_scope(**over)).scientific_native())


def _record(ident: str, els: Any, ref: Any) -> StructureRecord:
    return StructureRecord(
        id=ident,
        atoms=tuple(els),
        coordinates=tuple((float(x), float(y), float(z)) for x, y, z in ref),
        charge=0,
        multiplicity=1,
    )


def _driving_metal() -> StructureRecord:
    return _record("m1", *_toy())


def _driving_free() -> StructureRecord:
    return _record("f1", *_embed("C[C@H]1CC[C@@H](O)C1")[1:])


def _item(key: str, records: list[StructureRecord], results: Any = None) -> WorkItem:
    named = WorkItemInputs(
        structures=FrozenDict({"structure": StructureSet(tuple(records))}),
        results=results if results is not None else FrozenDict({}),
    )
    return WorkItem(
        id=make_work_item_id(key),
        logical_key=key,
        step_id="cg",
        named_inputs=named,
        resources=ResourceRequest(cores_per_item=2, memory_per_item_bytes=2**30),
        semantic_digest="sha256:" + hashlib.sha256(key.encode()).hexdigest(),
        ordinal=0,
    )


def _sci(native: FrozenDict, seed: Any = 7, **over: Any) -> SimpleNamespace:
    base: dict[str, Any] = {"seed": seed, "native": native, "overrides": FrozenDict({})}
    base.update(over)
    return SimpleNamespace(**base)


def _ctx(sci: Any, run_root: str, fake: Path | None, **over: Any) -> ItemExecutionContext:
    params: dict[str, Any] = {
        "step_id": "cg",
        "scientific": sci,
        "scientific_defaults": SimpleNamespace(),
        "adapter": None,
        "profile": None,
        "supervisor": NativeProcessSupervisor(),
        "run_root": run_root,
        "poll_interval_seconds": 0.01,
    }
    if fake is not None:
        params["execution_binding"] = ExecutionBinding(binding_id="t", executable=str(fake))
    params.update(over)
    return ItemExecutionContext(**params)


def _run(native, driving, fake, tmp, seed=7, should_cancel=None, **kw):
    item = _item("cg-item", [driving])
    ctx = _ctx(_sci(native, seed), str(tmp), fake, **kw)
    return ConfgenExecutor().execute(item, ctx, should_cancel=should_cancel), item


def _throw(exc: Exception) -> Any:

    def _raise(*a: Any, **k: Any) -> Any:
        raise exc

    return _raise


def test_schema_rejections() -> None:
    for extra in (
        {"sampling": {"cap": 2}},
        {"rings": [{"id": "r", "atoms": [1, 2, 3, 4]}]},
        {
            "torsions": [
                {"id": "t", "bond": [1, 2], "model": "relative_rotation_grid", "angles": [0.0]}
            ]
        },
        {"paths": [{"start": 1, "end": 2, "move": "start", "step": 120}]},
        {"limits": {"max_declared_states": 10}},
        {"exclusions": []},
        {"stereochemistry": {}},
        {"overrides": {}},
        {"strict_path_bond_check": True},
        {
            "coordination": {
                "metal_center": 1,
                "binding_sites": _SITES,
                "shapes": ["square_planar"],
                "backend": "rigid",
            }
        },
        {"tolerances": {"clash_threshold": 0.5}},
    ):
        with pytest.raises(ValidationError):
            ConfgenModelV3.model_validate(_scope(**extra))
    with pytest.raises(ValidationError, match="seed"):
        ConfgenModelV3.model_validate(_scope(seed=None))
    with pytest.raises(ValidationError, match="at least 1 item"):
        ConfgenModelV3.model_validate(
            _scope(coordination={"metal_center": 1, "binding_sites": _SITES, "shapes": []})
        )
    with pytest.raises(ValidationError, match="removed"):
        ConfgenModelV3.model_validate({"schema_version": 3})
    for search in (
        {"starts": 0},
        {"small_ring_torsions": "x"},
        {"embed_timeout_seconds": -1},
        {"max_cycles": 0},
        {"bond_scale": 0.0},
    ):
        with pytest.raises(ValidationError):
            ConfgenModelV3.model_validate(_scope(search={**_SEARCH, **search}))
    assert _confgen_seed_requirement({"schema_version": 4}) is not None
    doc = {
        "schema": "confflow.workflow.v4",
        "inputs": {"structures": {"kind": "structure", "cardinality": "many"}},
        "global": {"scientific_defaults": {"charge": 0, "multiplicity": 1}},
        "steps": [
            {
                "id": "cg",
                "executor": "confgen",
                "bindings": {"structure": {"source": {"run": "structures"}}},
                "confgen": _scope(seed=None),
            }
        ],
    }
    assert not compile_workflow(doc).ok


def test_schema_search_ok_and_wire_identity() -> None:
    model = ConfgenModelV3.model_validate(_scope())
    assert model.search is not None and model.search.starts == 2
    assert model.search.small_ring_torsions == "both" and model.search.fragment_charges == []
    assert model.scientific_native()["search"]["starts"] == 2
    assert ConfgenModelV3.model_validate(_scope(search=None)).search is None
    now = ConfgenModelV3.model_validate({"schema_version": 4, "seed": 1}).scientific_native()
    assert "search" not in now
    digest = hashlib.sha256(json.dumps(now, sort_keys=True).encode()).hexdigest()
    assert digest == _WIRE_V4_DIGEST


def test_schema_v3_rejected_with_removal_message() -> None:
    with pytest.raises(ValidationError, match="ring, torsion, path"):
        ConfgenModelV3.model_validate({"schema_version": 3, "seed": 1})
    assert "schema_version: 4" in REMOVED_ENGINES_MESSAGE


def test_removal_message_spelling_is_pinned() -> None:
    from confflow.execution.confgen_executor import _REMOVED_ENGINES_MESSAGE as _exec_msg
    from confflow.producer.intent.capabilities.confgen import (
        REMOVED_ENGINES_MESSAGE as _cap_msg,
    )
    from confflow.science.confgen.search_spec import REMOVED_ENGINES_MESSAGE as _spec_msg

    assert _exec_msg == _spec_msg == _cap_msg == REMOVED_ENGINES_MESSAGE


def test_search_spec_rejections() -> None:
    from confflow.science.confgen.search_spec import (
        normalize_search_spec,
        resolve_search_coordination,
        resolve_search_starts,
    )

    base: dict[str, Any] = {"schema_version": 4, "index_base": 1, "seed": 1}
    with pytest.raises(ValueError, match="ring, torsion, path"):
        normalize_search_spec({"schema_version": 3, "seed": 1})
    with pytest.raises(ValueError, match="unknown keys"):
        normalize_search_spec({**base, "rings": []})
    with pytest.raises(ValueError, match="unknown keys"):
        normalize_search_spec({**base, "tolerances": {"clash_threshold": 0.5}})
    with pytest.raises(ValueError, match="unknown keys"):
        normalize_search_spec({**base, "coordination": {"metal_center": 1, "backend": "rigid"}})
    coord = {
        "metal_center": 0,
        "binding_sites": [{"id": f"s{i}", "atoms": [i + 1]} for i in range(4)],
        "shapes": ["tetrahedral"],
    }
    with pytest.raises(ValueError, match="unknown keys"):
        resolve_search_coordination({**coord, "budgets": {}})
    bad_site = dict(coord)
    bad_site["binding_sites"] = [
        {"id": f"s{i}", "atoms": [i + 1], **({"treatment": "enumerate"} if i == 0 else {})}
        for i in range(4)
    ]
    with pytest.raises(ValueError, match="unknown keys"):
        resolve_search_coordination(bad_site)
    bad_constraint = dict(coord)
    bad_constraint["constraints"] = [
        {"id": "C00", "sites": ["s0", "s1"], "provenance": "p", "proof": {}}
    ]
    with pytest.raises(ValueError, match="unknown keys"):
        resolve_search_coordination(bad_constraint)
    with pytest.raises(ValueError, match="exactly one"):
        resolve_search_coordination({**coord, "shapes": ["auto"]})
    with pytest.raises(ValueError, match="at least one|exactly one"):
        resolve_search_coordination({**coord, "shapes": []})
    with pytest.raises(ValueError, match="integer >= 1"):
        resolve_search_starts({"starts": 0}, has_coordination=True)


def _summary(out: Any, tmp: Path, role: str = "search_summary") -> dict[str, Any]:
    ref = next(r for r in out.artifacts if r.role == role)
    assert ref.locator.path is not None
    return json.loads((tmp / ref.locator.path).read_text(encoding="utf-8"))


def test_metal_search_completed(tmp_path: Path) -> None:
    driving = _driving_metal()
    out, _ = _run(_native(), driving, _write_fake(tmp_path), tmp_path)
    assert out.status.value == "completed", [str(d.message) for d in out.diagnostics]
    summary = _summary(out, tmp_path)
    assert len(out.structures) == summary["totals"]["passed"] >= 1
    assert [r.ordinal for r in out.structures] == list(range(len(out.structures)))
    assert [r.id for r in out.structures] == [
        f"cg-item:structure:conformer:{i}" for i in range(len(out.structures))
    ]
    assert all(r.role == "conformer" for r in out.structures)
    assert all(tuple(r.atoms) == tuple(driving.atoms) for r in out.structures)
    assert all(r.parent_ids == ("m1",) for r in out.structures)
    assert all(r.charge == 0 and r.multiplicity == 1 for r in out.structures)
    first_meta = dict(out.structures[0].metadata)
    assert first_meta[CONFORMER_MEMBER_METADATA_KEY] == 0 and first_meta["seed"] == 7
    assert first_meta["rel_kcal"] == 0.0 and first_meta["search_target"].startswith("t")
    energies = [dict(r.metadata)["energy_eh"] for r in out.structures]
    assert energies == sorted(energies)
    assert len(out.results) == 0
    assert {r.role for r in out.artifacts} >= {"search_summary", "search_structures"}
    info = [d for d in out.diagnostics if d.code == "confgen_completed"]
    assert len(info) == 1 and "passed" in info[0].message


def test_metal_free_search_completed(tmp_path: Path) -> None:
    driving = _driving_free()
    scope = _scope(seed=5, coordination=None)
    native = FrozenDict(ConfgenModelV3.model_validate(scope).scientific_native())
    out, _ = _run(native, driving, _write_fake(tmp_path), tmp_path, seed=5)
    assert out.status.value == "completed", [str(d.message) for d in out.diagnostics]
    assert len(out.structures) >= 1 and len(out.results) == 0
    assert all(tuple(r.atoms) == tuple(driving.atoms) for r in out.structures)


def test_zero_passing_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_XTB_MODE", "noconverge")
    out, _ = _run(_native(), _driving_metal(), _write_fake(tmp_path), tmp_path)
    assert out.status.value == "failed"
    errors = [d for d in out.diagnostics if d.is_error]
    assert len(errors) == 1 and "converged=6" in errors[0].message
    assert _summary(out, tmp_path)["totals"]["passed"] == 0


def test_missing_binding_failed_before_launch(tmp_path: Path) -> None:
    out, item = _run(_native(), _driving_metal(), None, tmp_path)
    assert out.status.value == "failed"
    assert "xTB executable" in out.diagnostics[0].message
    attempt = Path(_ctx(_sci(_native()), str(tmp_path), None).attempt_dir(item))
    assert not (attempt / "search_run").exists()


def test_pre_launch_failures(tmp_path: Path) -> None:
    fake = _write_fake(tmp_path)
    driving = _driving_metal()
    out, _ = _run(_native(), driving, fake, tmp_path, seed=8)
    assert out.status.value == "failed" and "seed conflict" in out.diagnostics[0].message
    native = FrozenDict({k: v for k, v in dict(_native()).items() if k != "seed"})
    out, _ = _run(native, driving, fake, tmp_path, seed=None)
    assert out.status.value == "failed" and "explicit top-level seed" in out.diagnostics[0].message
    out, _ = _run(_native(), driving, fake, tmp_path, supervisor=None)
    assert out.status.value == "failed" and "supervisor" in out.diagnostics[0].message
    out, _ = _run(
        _native(topology={"bonds": [[1, 99]]}),
        driving,
        fake,
        tmp_path,
    )
    assert out.status.value == "failed" and "spec rejected" in out.diagnostics[0].message
    auto = dict(ConfgenModelV3.model_validate(_scope()).scientific_native())
    auto["coordination"] = {"metal_center": 1, "binding_sites": _SITES, "shapes": "auto"}
    out, _ = _run(FrozenDict(auto), driving, fake, tmp_path)
    assert out.status.value == "failed" and "exactly one" in out.diagnostics[0].message
    frag = {**_SEARCH, "fragment_charges": [{"atom": 99, "charge": 0}]}
    out, _ = _run(_native(search=frag), driving, fake, tmp_path)
    assert out.status.value == "failed" and "out of range" in out.diagnostics[0].message
    naked = StructureRecord(id="n", atoms=driving.atoms, coordinates=driving.coordinates)
    out, _ = _run(_native(), naked, fake, tmp_path)
    assert out.status.value == "failed" and "charge" in out.diagnostics[0].message


def test_cancelled(tmp_path: Path) -> None:
    native = _native(search={**_SEARCH, "starts": 1})
    fake = _write_fake(tmp_path)
    out, _ = _run(native, _driving_metal(), fake, tmp_path, should_cancel=lambda: True)
    assert out.status.value == "cancelled"


def test_launcher_outcomes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    native, driving, fake = _native(), _driving_metal(), _write_fake(tmp_path)
    item = _item("q", [driving])
    ctx = _ctx(_sci(native), str(tmp_path), fake)
    monkeypatch.setattr(WorkItemExecutor, "launch_and_wait", _throw(QuotaCancelled("stop")))
    assert ConfgenExecutor().execute(item, ctx).status.value == "cancelled"
    monkeypatch.setattr(WorkItemExecutor, "launch_and_wait", _throw(QuotaError("nope")))
    assert ConfgenExecutor().execute(item, ctx).status.value == "failed"
    monkeypatch.setattr(WorkItemExecutor, "launch_and_wait", lambda *a, **k: None)
    assert ConfgenExecutor().execute(item, ctx).status.value == "failed"
    run = ConfgenExecutor().execute(item, ctx, should_cancel=lambda: True)
    assert run.status.value == "cancelled"
    ended = NativeExecutionResult(exit_code=None, wall_time_seconds=0.1, timed_out=True)
    monkeypatch.setattr(WorkItemExecutor, "launch_and_wait", lambda *a, **k: (ended, None))
    out = ConfgenExecutor().execute(item, ctx)
    assert out.status.value == "failed" and "did not exit cleanly" in out.diagnostics[0].message
    stuck = (ended, CancelOutcome(confirmed=False, detail="stuck"))
    monkeypatch.setattr(WorkItemExecutor, "launch_and_wait", lambda *a, **k: stuck)
    out = ConfgenExecutor().execute(item, ctx)
    assert out.status.value == "failed" and "could not be confirmed" in out.diagnostics[0].message


def test_worker_exit_three_failed(tmp_path: Path) -> None:
    out, _ = _run(_native(), _driving_metal(), Path("/nonexistent/xtb"), tmp_path)
    assert out.status.value == "failed"
    assert "exit code 3" in out.diagnostics[0].message


def test_import_validation(tmp_path: Path) -> None:
    driving = StructureRecord(
        id="d", atoms=("H", "H"), coordinates=((0.0, 0.0, 0.0), (0.0, 0.0, 0.74))
    )
    rows = [{"target": "t00", "start": 0, "energy_eh": -1.0, "passed": True, "failed_checks": []}]
    totals = {"targets": 1, "generated": 1, "passed": 1, "relaxed": 1}
    frame = "2\ntarget=t00 start=0 energy_eh=-1.000000 rel_kcal=0.000\nH 0 0 0\nH 0 0 0.74\n"

    def _write(tag: str, summary: Any, xyz: str | None) -> Path:
        rundir = tmp_path / tag
        rundir.mkdir(exist_ok=True)
        (rundir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        if xyz is not None:
            (rundir / "structures.xyz").write_text(xyz, encoding="utf-8")
        return rundir

    ok_summary = {"targets": [], "structures": rows, "fragment_charges": [], "totals": totals}
    totals_out, _, ordered, frames = search_run._read_validated(
        driving, _write("ok", ok_summary, frame)
    )
    assert totals_out["passed"] == 1 and len(ordered) == len(frames) == 1
    for tag, summary, xyz in (
        ("bad", "nope", frame),
        ("lst", [1], frame),
        ("cnt", dict(ok_summary, totals=dict(totals, passed=0)), frame),
        ("mxyz", ok_summary, None),
        ("atm", ok_summary, frame.replace("H 0", "He 0")),
        ("nrg", ok_summary, frame.replace("energy_eh=-1.000000", "energy_eh=-0.500000")),
        ("tag", ok_summary, "2\nframe\nH 0 0 0\nH 0 0 0.74\n"),
        ("key", {}, frame),
    ):
        with pytest.raises(search_run._ImportError):
            search_run._read_validated(driving, _write(tag, summary, xyz))


def test_upstream_results_are_plain_structures(tmp_path: Path) -> None:
    """Upstream confgen results carry no chained state: members are plain structures."""
    driving = _driving_metal()
    record = ScientificResult(
        kind="confgen_state",
        value={"schema_version": 4},
        subject_structure_id="m1",
        source_step_id="up",
        source_work_item_id="wu",
        result_id=make_result_id(
            step_id="up",
            kind="confgen_state",
            subject_structure_id="m1",
            producer_digest="sha256:" + "ab" * 32,
        ),
    )
    results = FrozenDict({"confgen_state": ResultSet((record,))})
    out = ConfgenExecutor().execute(
        _item("chain", [driving], results),
        _ctx(_sci(_native()), str(tmp_path), _write_fake(tmp_path)),
    )
    assert out.status.value == "completed", [str(d.message) for d in out.diagnostics]
    assert len(out.results) == 0
    assert all("input_confgen_state" not in dict(m.metadata) for m in out.structures)


def test_v3_scope_fails_with_removal_message(tmp_path: Path) -> None:
    driving = _driving_metal()
    native = FrozenDict({"schema_version": 3, "seed": 7, "search": dict(_SEARCH)})
    out, _ = _run(native, driving, _write_fake(tmp_path), tmp_path)
    assert out.status.value == "failed"
    assert "ring, torsion, path" in out.diagnostics[0].message


def test_default_starts_resolution(tmp_path: Path) -> None:
    """Absent search / null starts resolve to 8 with coordination, 400 without."""
    from confflow.science.confgen.search_spec import resolve_search_starts

    assert resolve_search_starts(None, has_coordination=True) == 8
    assert resolve_search_starts(None, has_coordination=False) == 400
    assert resolve_search_starts({"starts": None}, has_coordination=True) == 8
    assert resolve_search_starts({"starts": 3}, has_coordination=False) == 3
    driving = _driving_metal()
    scope = _scope(seed=7)
    scope.pop("search", None)
    native = FrozenDict(ConfgenModelV3.model_validate(scope).scientific_native())
    out, _ = _run(native, driving, _write_fake(tmp_path), tmp_path)
    assert out.status.value == "completed", [str(d.message) for d in out.diagnostics]
    jobs = list(tmp_path.rglob("search_job.json"))
    assert len(jobs) == 1
    assert json.loads(jobs[0].read_text())["settings"]["starts"] == 8
    summary = _summary(out, tmp_path)
    assert summary["settings"]["starts"] == 8
    assert dict(out.structures[0].metadata)["starts"] == 8


def test_end_to_end_binding(tmp_path: Path) -> None:
    from confflow.application.v4_run import V4RunApplication, V4RunRequest, import_xyz
    from confflow.workflow.v4.assembly import RunInputs

    els, ref = _toy()
    xyz_text = f"{len(els)}\ntoy\n" + "".join(
        f"{e} {x} {y} {z}\n" for e, (x, y, z) in zip(els, ref)
    )
    fake = _write_fake(tmp_path)
    topology = {"add_bond": [{"atoms": [2, 4], "kind": "FORMING"}]}
    step = {
        "id": "cg",
        "executor": "confgen",
        "bindings": {"structure": {"source": {"run": "structures"}}},
        "confgen": {
            **_scope(),
            "topology": topology,
            "search": {**_SEARCH, "starts": 1},
            "seed": 11,
        },
    }
    document = {
        "schema": "confflow.workflow.v4",
        "inputs": {
            "structures": {
                "kind": "structure",
                "cardinality": "many",
                "charge": 0,
                "multiplicity": 1,
            }
        },
        "steps": [step],
    }
    structures = import_xyz(xyz_text, source_name="toy.xyz")
    first = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
        V4RunRequest(
            workflow_document=document,
            run_inputs=RunInputs(structures=FrozenDict({"structures": structures})),
            run_root=str(tmp_path / "run"),
            executables=FrozenDict({"xtb": str(fake)}),
        )
    )
    assert first.status == "completed", [(s.step_id, s.status) for s in first.step_results]
    summaries = list((tmp_path / "run").rglob("summary.json"))
    assert summaries
    assert json.loads(Path(summaries[0]).read_text())["totals"]["passed"] >= 1
    bare = V4RunApplication(supervisor=NativeProcessSupervisor()).run(
        V4RunRequest(
            workflow_document=document,
            run_inputs=RunInputs(structures=FrozenDict({"structures": structures})),
            run_root=str(tmp_path / "run2"),
        )
    )
    assert bare.status == "failed"
