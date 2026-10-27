#!/usr/bin/env python3

"""Optional donor states in the confgen search worker and step: loop, merge, publication."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from confflow.domain import FrozenDict, StructureSet
from confflow.domain.resources import ResourceRequest
from confflow.domain.structure import StructureRecord
from confflow.domain.work_item import WorkItem, WorkItemInputs, make_work_item_id
from confflow.execution import confgen_search_run as run_mod
from confflow.execution import confgen_search_worker as worker
from confflow.execution.confgen_executor import ConfgenExecutor
from confflow.execution.contracts import ExecutionBinding
from confflow.execution.output_identity import CONFORMER_MEMBER_METADATA_KEY
from confflow.execution.process import NativeProcessSupervisor
from confflow.execution.work_item_executor import ItemExecutionContext
from confflow.science.confgen import search
from confflow.science.confgen.explore import ExploreSettings
from confflow.science.confgen.search_spec import donor_states
from confflow.workflow.v4.confgen_schema import ConfgenModelV3
from tests.v4._dg_search_helpers import _toy, _write_fake, build_topology

HARTREE = 627.5094740631
E0 = -50.0
_SITES = [
    {"id": "s0", "atoms": [2]},
    {"id": "s1", "atoms": [3]},
    {"id": "s2", "atoms": [4]},
    {"id": "s3", "atoms": [5], "optional": True},
]
_OPTIONAL_SCOPE = {
    "metal_center": 1,
    "binding_sites": _SITES,
    "shapes": ["input"],
}
_SEARCH = {
    "budget": 12,
    "starts": 2,
    "embed_timeout_seconds": 20,
    "max_cycles": 100,
    "bond_scale": 1.25,
}


def _kcal(value: float) -> float:
    return E0 + value / HARTREE


def _rec(
    target: str, start: int, energy: float, bound: dict[int, bool] | None, coords: Any
) -> search.SearchRecord:
    return search.SearchRecord(
        target=target,
        start=start,
        small_ring_torsions=False,
        energy_eh=energy,
        passed=True,
        coords=np.asarray(coords, dtype=float),
        optional_bound=bound,
    )


def _run_of(records: list[search.SearchRecord], budget: int = 6) -> search.SearchRun:
    targets = (search.SearchTarget("t00", generated=budget, relaxed=budget, passed=len(records)),)
    return search.SearchRun(
        targets=targets,
        records=tuple(records),
        passing=tuple(records),
        totals={"targets": 1, "generated": budget, "relaxed": budget, "passed": len(records)},
        stats={"stop": "budget", "release": "done", "rounds": 3},
    )


def _geom(ref: np.ndarray, bound: bool) -> np.ndarray:
    """Toy geometry with donor atom 4 bound (input distance) or pulled out to 4.5 A."""
    coords = np.array(ref, dtype=float)
    if not bound:
        coords[4] = (0.0, -4.5, 0.0)
    return coords


def _job(
    states: list[tuple[str, Any]], settings: ExploreSettings, graph: Any, ref: np.ndarray
) -> dict[str, Any]:
    return {
        "settings": settings,
        "donor_states": states,
        "graph": graph,
        "reference": ref,
        "spec": SimpleNamespace(metal_center=0),
        "shape": None,
        "donor_sites": [("s3", 4)],
    }


def _toy_graph() -> tuple[Any, np.ndarray]:
    els, ref = _toy()
    graph = build_topology(els, ref, (0, [1, 2, 3, 4], [(1, 3)], [], 1.25), "square_planar")[0]
    return graph, ref


# ----------------------------------------------------------------- state loop (fake explore)


def test_state_loop_splits_budget_starts_and_seeds(monkeypatch: pytest.MonkeyPatch) -> None:
    graph, ref = _toy_graph()
    calls: list[ExploreSettings] = []

    def fake_explore(*args: Any, **kwargs: Any) -> search.SearchRun:
        calls.append(args[4])
        return _run_of([])

    monkeypatch.setattr(worker, "explore", fake_explore)
    settings = ExploreSettings(budget=13, min_budget=13, starts=5, seed=7, window_kcal=6.0)
    states = [("s3+", None), ("s3-", None)]
    worker._explore_states(_job(states, settings, graph, ref), None, map, None)
    assert [c.budget for c in calls] == [6, 6]
    assert [c.min_budget for c in calls] == [6, 6]
    assert [c.starts for c in calls] == [2, 2]
    assert [c.seed for c in calls] == [7, 8]
    assert all(c.time_limit_seconds is None for c in calls)


def test_state_loop_budget_floor_and_time_split(monkeypatch: pytest.MonkeyPatch) -> None:
    graph, ref = _toy_graph()
    calls: list[ExploreSettings] = []

    def fake_explore(*args: Any, **kwargs: Any) -> search.SearchRun:
        calls.append(args[4])
        return _run_of([])

    monkeypatch.setattr(worker, "explore", fake_explore)
    settings = ExploreSettings(
        budget=1, min_budget=1, starts=None, seed=1, time_limit_seconds=100.0
    )
    states = [("s3+", None), ("s3-", None)]
    worker._explore_states(_job(states, settings, graph, ref), None, map, None)
    assert [c.budget for c in calls] == [1, 1]
    assert [c.starts for c in calls] == [None, None]
    assert 0.0 < calls[0].time_limit_seconds <= 50.0
    assert 0.0 < calls[1].time_limit_seconds <= 100.0


# ----------------------------------------------------------------- merge


def test_merge_keeps_window_lowest_per_state_and_dedupes() -> None:
    graph, ref = _toy_graph()
    bound, free = _geom(ref, True), _geom(ref, False)
    run_bound = _run_of(
        [
            _rec("t00", 0, _kcal(0.0), {4: True}, bound),  # lowest overall
            _rec("t01", 1, _kcal(1.0), {4: True}, bound),  # in window
            _rec("t00", 2, _kcal(8.0), {4: True}, bound),  # outside window, not lowest
        ]
    )
    run_free = _run_of(
        [
            _rec("t00", 0, _kcal(0.0), {4: True}, bound),  # duplicate of the bound run's lowest
            _rec("t02", 1, _kcal(5.0), {4: True}, bound),  # measured bound, in window
            _rec(
                "t01", 2, _kcal(12.0), {4: False}, free
            ),  # detached state's lowest, outside window
            _rec("t01", 3, _kcal(15.0), {4: False}, free),  # outside window, not lowest
        ]
    )
    settings = ExploreSettings(window_kcal=6.0, seed=3, freeze_core=())
    job = _job([("s3+", None), ("s3-", None)], settings, graph, ref)
    sub = ExploreSettings(budget=3, seed=3)
    merged = worker._merge_donor_runs(job, [("s3+", sub, run_bound), ("s3-", sub, run_free)])
    energies = [round((r.energy_eh - _kcal(0.0)) * HARTREE, 3) for r in merged.passing]
    assert energies == [0.0, 1.0, 5.0, 12.0]
    states = [tuple(sorted((r.optional_bound or {}).items())) for r in merged.passing]
    assert states == [((4, True),), ((4, True),), ((4, True),), ((4, False),)]
    assert len({r.start for r in merged.passing}) == len(merged.passing)
    assert merged.totals["passed"] == len(merged.passing) == len(merged.records)
    rows = merged.stats["donor_states"]
    assert [row["label"] for row in rows] == ["s3+", "s3-"]
    assert [row["published"] for row in rows] == [2, 2]
    assert rows[0]["lowest_rel_kcal"] == pytest.approx(0.0, abs=1e-6)
    assert rows[1]["lowest_rel_kcal"] == pytest.approx(0.0, abs=1e-6)
    assert [row["budget"] for row in rows] == [3, 3]
    assert merged.stats["stop"] == "budget"
    assert merged.targets[0].passed == sum(1 for r in merged.passing if r.target == "t00")


def test_merge_without_outside_lowest_keeps_only_window() -> None:
    graph, ref = _toy_graph()
    run_a = _run_of([_rec("t00", 0, _kcal(0.0), {4: True}, _geom(ref, True))])
    run_b = _run_of([_rec("t00", 0, _kcal(2.0), {4: False}, _geom(ref, False))])
    settings = ExploreSettings(window_kcal=6.0, seed=1)
    merged = worker._merge_donor_runs(
        _job([("s3+", None), ("s3-", None)], settings, graph, ref),
        [("s3+", settings, run_a), ("s3-", settings, run_b)],
    )
    assert len(merged.passing) == 2
    rows = merged.stats["donor_states"]
    assert [row["published"] for row in rows] == [1, 1]


# ----------------------------------------------------------------- comment and labels


def test_comment_gains_donor_state_only_with_optional_donors() -> None:
    _, ref = _toy_graph()
    bound = _rec("t00", 3, _kcal(0.0), {4: True}, ref)
    free = _rec("t00", 3, _kcal(0.0), None, ref)
    assert worker._comment(bound, _kcal(0.0), [("s3", 4)]).endswith(" donors=s3+")
    assert "donors=" not in worker._comment(bound, _kcal(0.0))
    assert "donors=" not in worker._comment(free, _kcal(0.0), [("s3", 4)])
    detached = _rec("t00", 3, _kcal(0.0), {4: False}, ref)
    assert worker.donor_state_label([("s0", 1), ("s3", 4)], {4: False}) == "s3-"
    assert worker._comment(detached, _kcal(0.0), [("s3", 4)]).endswith(" donors=s3-")


def test_donor_states_without_optional_sites_is_single_unlabelled_state() -> None:
    native = {"coordination": {"metal_center": 1, "binding_sites": _SITES[:3], "shapes": ["input"]}}
    states = donor_states(native)
    assert len(states) == 1 and states[0].label == ""


# ----------------------------------------------------------------- step level (fake xTB)


def _item(native: FrozenDict, driving: StructureRecord, key: str = "cg-item") -> WorkItem:
    named = WorkItemInputs(
        structures=FrozenDict({"structure": StructureSet((driving,))}),
        results=FrozenDict({}),
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


def _run_step(native: FrozenDict, tmp: Path) -> Any:
    els, ref = _toy()
    driving = StructureRecord(
        id="m1",
        atoms=tuple(els),
        coordinates=tuple((float(x), float(y), float(z)) for x, y, z in ref),
        charge=0,
        multiplicity=1,
    )
    sci = SimpleNamespace(seed=7, native=native, overrides=FrozenDict({}))
    ctx = ItemExecutionContext(
        step_id="cg",
        scientific=sci,
        scientific_defaults=SimpleNamespace(),
        adapter=None,
        profile=None,
        supervisor=NativeProcessSupervisor(),
        run_root=str(tmp),
        poll_interval_seconds=0.01,
        execution_binding=ExecutionBinding(binding_id="t", executable=str(_write_fake(tmp))),
    )
    return ConfgenExecutor().execute(_item(native, driving), ctx, should_cancel=None)


def _native(scope: dict[str, Any]) -> FrozenDict:
    doc = {"schema_version": 4, "coordination": scope, "seed": 7, "search": dict(_SEARCH)}
    return FrozenDict(ConfgenModelV3.model_validate(doc).scientific_native())


def _artifact(out: Any, tmp: Path, role: str) -> Path:
    ref = next(r for r in out.artifacts if r.role == role)
    return tmp / ref.locator.path


def test_optional_donor_search_publishes_states(tmp_path: Path) -> None:
    out = _run_step(_native(_OPTIONAL_SCOPE), tmp_path)
    assert out.status.value == "completed", [str(d.message) for d in out.diagnostics]
    summary = json.loads(_artifact(out, tmp_path, "search_summary").read_text(encoding="utf-8"))
    rows = summary["search"]["donor_states"]
    assert [row["label"] for row in rows] == ["s3+", "s3-"]
    assert [row["budget"] for row in rows] == [6, 6]
    diags = [d for d in out.diagnostics if d.code == "confgen_donor_states"]
    assert len(diags) == 1 and diags[0].severity.value == "info"
    assert len(dict(diags[0].details)["states"]) == 2
    for member in out.structures:
        meta = dict(member.metadata)
        assert meta["search_optional_bound"] == {"5": meta["search_optional_bound"]["5"]}
        assert meta["search_donor_state"] == (
            "s3+" if meta["search_optional_bound"]["5"] else "s3-"
        )
        assert isinstance(meta[CONFORMER_MEMBER_METADATA_KEY], int)
    xyz = _artifact(out, tmp_path, "search_structures").read_text(encoding="utf-8")
    comments = [line for line in xyz.splitlines() if "energy_eh=" in line]
    assert comments and all(" donors=s3" in line for line in comments)


def test_no_optional_donor_search_has_no_new_keys(tmp_path: Path) -> None:
    plain = {
        "metal_center": 1,
        "binding_sites": [{"id": s["id"], "atoms": s["atoms"]} for s in _SITES],
        "shapes": ["square_planar"],
    }
    out = _run_step(_native(plain), tmp_path)
    assert out.status.value == "completed", [str(d.message) for d in out.diagnostics]
    summary = json.loads(_artifact(out, tmp_path, "search_summary").read_text(encoding="utf-8"))
    assert "donor_states" not in summary["search"]
    assert not [d for d in out.diagnostics if d.code == "confgen_donor_states"]
    for member in out.structures:
        meta = dict(member.metadata)
        assert "search_optional_bound" not in meta and "search_donor_state" not in meta
    xyz = _artifact(out, tmp_path, "search_structures").read_text(encoding="utf-8")
    assert "donors=" not in xyz


# ------------------------------------------------- measured-state counts and re-binding


def test_merge_counts_published_by_measured_state_and_rebinding() -> None:
    graph, ref = _toy_graph()
    bound_spec = SimpleNamespace(metal_center=0, detached_donors=())
    detached_spec = SimpleNamespace(metal_center=0, detached_donors=(4,))
    run_bound = _run_of(
        [
            _rec("t00", 0, _kcal(0.0), {4: True}, _geom(ref, True)),
            _rec("t00", 1, _kcal(1.0), {4: False}, _geom(ref, False)),  # detached: a re-detach
        ]
    )
    shifted = _geom(ref, True)
    shifted[1] += (0.3, 0.0, 0.0)  # a distinct geometry, so it is not deduplicated
    run_free = _run_of(
        [_rec("t01", 0, _kcal(2.0), {4: True}, shifted)]
    )  # detached sub-search whose donor re-bound (measured bound)
    settings = ExploreSettings(window_kcal=50.0, seed=3, freeze_core=())
    job = _job([("s3+", bound_spec), ("s3-", detached_spec)], settings, graph, ref)
    sub = ExploreSettings(budget=3, seed=3)
    merged = worker._merge_donor_runs(job, [("s3+", sub, run_bound), ("s3-", sub, run_free)])
    rows = merged.stats["donor_states"]
    assert [row["published"] for row in rows] == [2, 1]  # per sub-search (ownership)
    assert [row["passed"] for row in rows] == [2, 1]
    assert rows[0]["rebound"] == {} and rows[0]["detached"] == {"s3": 1}
    assert rows[1]["rebound"] == {"s3": 1} and rows[1]["detached"] == {}
    measured = {row["label"]: row["published"] for row in merged.stats["donor_measured"]}
    assert measured == {"s3+": 2, "s3-": 1}


def test_donor_state_diagnostics_message_and_missing_warning() -> None:
    rows = [
        {"label": "s3+", "passed": 6, "published": 6, "lowest_rel_kcal": 0.0,
         "rebound": {}, "detached": {"s3": 1}},  # fmt: skip
        {"label": "s3-", "passed": 5, "published": 0, "lowest_rel_kcal": 7.14,
         "rebound": {"s3": 5}, "detached": {}},  # fmt: skip
    ]  # fmt: skip
    measured = [
        {"label": "s3+", "published": 6, "lowest_rel_kcal": 0.0},
        {"label": "s3-", "published": 0, "lowest_rel_kcal": None},
    ]
    item = SimpleNamespace(id="w1", logical_key="k1")
    diags = run_mod._donor_states_diagnostics(rows, measured, "step", item)
    info = [d for d in diags if d.code == "confgen_donor_states"]
    assert len(info) == 1 and info[0].severity.value == "info"
    assert info[0].message == (
        "optional donor states (measured): s3+: 6 published, lowest +0.00; s3-: 0 published. "
        "Sub-searches: s3+ search: 6 passing structures, 1 detached s3 (lowest +0.00 kcal); "
        "s3- search: 5 passing structures, 5 re-bound s3 (lowest +7.14 kcal)."
    )
    missing = [d for d in diags if d.code == "confgen_donor_state_missing"]
    assert len(missing) == 1 and missing[0].severity.value == "warning"
    assert tuple(dict(missing[0].details)["missing"]) == ("s3-",)
    assert "s3- (its sub-search produced 5 passing structures, which changed state" in (
        missing[0].message
    )
    assert "5 re-bound s3" in missing[0].message and "none was published" in missing[0].message


def test_donor_state_diagnostics_no_warning_when_every_state_is_published() -> None:
    measured = [{"label": "s3+", "published": 2, "lowest_rel_kcal": 0.0},
                {"label": "s3-", "published": 1, "lowest_rel_kcal": 1.5}]  # fmt: skip
    rows = [{"label": "s3+", "passed": 2, "rebound": {}, "detached": {}, "lowest_rel_kcal": 0.0},
            {"label": "s3-", "passed": 1, "rebound": {}, "detached": {}, "lowest_rel_kcal": 1.5}]  # fmt: skip
    item = SimpleNamespace(id="w1", logical_key="k1")
    diags = run_mod._donor_states_diagnostics(rows, measured, "step", item)
    assert [d.code for d in diags] == ["confgen_donor_states"]
    assert "s3+ search: 2 passing structures, none changed donor state" in diags[0].message


def test_missing_state_without_passing_structures_says_so() -> None:
    measured = [{"label": "s3-", "published": 0, "lowest_rel_kcal": None}]
    rows = [{"label": "s3-", "passed": 0, "rebound": {}, "detached": {}, "lowest_rel_kcal": None}]
    item = SimpleNamespace(id="w1", logical_key="k1")
    diags = run_mod._donor_states_diagnostics(rows, measured, "step", item)
    missing = [d for d in diags if d.code == "confgen_donor_state_missing"]
    assert "s3- (its sub-search produced no passing structures)" in missing[0].message


def test_rebound_passing_structure_outside_window_is_counted_and_warned() -> None:
    graph, ref = _toy_graph()
    shifted = _geom(ref, True)
    shifted[1] += (0.3, 0.0, 0.0)  # distinct geometry, not deduplicated
    bound_spec = SimpleNamespace(metal_center=0, detached_donors=())
    detached_spec = SimpleNamespace(metal_center=0, detached_donors=(4,))
    run_bound = _run_of([_rec("t00", 0, _kcal(0.0), {4: True}, _geom(ref, True))])
    run_free = _run_of([_rec("t01", 0, _kcal(3.0), {4: True}, shifted)])  # re-bound, 3 kcal up
    settings = ExploreSettings(window_kcal=1.0, seed=3, freeze_core=())
    job = _job([("s3+", bound_spec), ("s3-", detached_spec)], settings, graph, ref)
    sub = ExploreSettings(budget=3, seed=3)
    merged = worker._merge_donor_runs(job, [("s3+", sub, run_bound), ("s3-", sub, run_free)])
    rows = {row["label"]: row for row in merged.stats["donor_states"]}
    assert rows["s3-"]["passed"] == 1 and rows["s3-"]["published"] == 0
    assert rows["s3-"]["rebound"] == {"s3": 1}
    assert rows["s3-"]["lowest_rel_kcal"] == pytest.approx(3.0, abs=1e-6)
    item = SimpleNamespace(id="w1", logical_key="k1")
    diags = run_mod._donor_states_diagnostics(
        merged.stats["donor_states"], merged.stats["donor_measured"], "step", item
    )
    info = [d for d in diags if d.code == "confgen_donor_states"][0]
    assert "s3- search: 1 passing structure, 1 re-bound s3 (lowest +3.00 kcal)" in info.message
    missing = [d for d in diags if d.code == "confgen_donor_state_missing"]
    assert (
        missing and "1 passing structure, which changed state (1 re-bound s3)" in missing[0].message
    )
