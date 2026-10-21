"""Tests for the iterative search ``confflow.science.confgen.explore`` with a fake engine.

The engine is defined here: relaxations return their input geometry and a deterministic energy
that depends on the central torsion of butane, so several distinct minima exist without any
real optimisation.  Budgets are tiny because DG embedding runs real RDKit.
"""

from __future__ import annotations

import math
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from confflow.science.confgen import explore, search
from confflow.science.confgen.calculator import (
    CAP_FIXED_ATOMS,
    CAP_HESSIAN,
    CalculatorInfo,
    RelaxOutcome,
    RelaxRequest,
)
from confflow.science.confgen.graph import EdgeType, TypedEdge
from confflow.science.confgen.identity import HARTREE_TO_KCAL, Identity
from confflow.science.confgen.rotors import dihedral
from tests.v4._dg_search_helpers import _SHAPE, _embed, _toy, build_topology


def _energy(xyz: np.ndarray) -> float:
    """Torsion-dependent energy of a butane-like chain (atoms 0-3 are the backbone)."""
    phi = dihedral(xyz, 0, 1, 2, 3)
    return -100.0 + 0.004 * (1.0 - math.cos(math.radians(3.0 * phi))) + 0.0005 * (
        1.0 - math.cos(math.radians(phi))
    )  # fmt: skip


class _FakeCalc:
    """Deterministic engine: relax returns the input geometry; hessian is positive definite."""

    def __init__(self, capabilities: frozenset[str] = frozenset()) -> None:
        self.info = CalculatorInfo(
            "fake", "fake", "0", frozenset(capabilities) | {"restrained_relax"}
        )
        self.requests: list[RelaxRequest] = []
        self.hessian_calls = 0

    def relax_many(self, requests):
        self.requests += list(requests)
        return [
            RelaxOutcome(
                coords=np.asarray(q.coords, dtype=float),
                energy_eh=_energy(np.asarray(q.coords)),
                converged=True,
            )
            for q in requests
        ]

    def hessian(self, request):
        self.hessian_calls += 1
        n3 = 3 * len(request.coords)
        a = np.random.default_rng(7).normal(size=(n3, n3))
        return a @ a.T + n3 * np.eye(n3)


def _butane():
    """Return (typed graph, reference) for metal-free n-butane."""
    _, els, ref = _embed("CCCC")
    graph, _, _ = build_topology(els, ref, (None, [], [], [], 1.25), None)
    return graph, ref


def _pt_toy():
    """Return (typed graph, spec, reference) for the square-planar Pt toy with one reaction pair."""
    els, ref = _toy()
    graph, spec, _ = build_topology(els, ref, (0, [1, 2, 3, 4], [(1, 3)], [], 1.25), _SHAPE)
    return graph, spec, ref


def _settings(**kw: Any) -> explore.ExploreSettings:
    base: dict[str, Any] = {"budget": 40, "window_kcal": 2.0, "starts": 4, "batch": 16}
    base.update(kw)
    return explore.ExploreSettings(**base)


def test_budget_is_never_exceeded() -> None:
    graph, ref = _butane()
    calc = _FakeCalc()
    run = explore.explore(graph, None, ref, None, _settings(budget=40), calc, map)
    assert run.totals["generated"] <= 40
    assert run.stats["relaxations"] <= 40
    assert run.stats["relaxations"] > 0
    assert run.stats["relaxations"] == run.totals["generated"]


def test_published_records_are_windowed_sorted_and_unique() -> None:
    graph, ref = _butane()
    run = explore.explore(graph, None, ref, None, _settings(budget=60), _FakeCalc(), map)
    recs = run.records
    assert recs, "the fake engine should publish at least one minimum"
    energies = [r.energy_eh for r in recs]
    assert energies == sorted(energies)
    lowest = min(energies)
    assert all((e - lowest) * HARTREE_TO_KCAL <= 2.0 + 1e-9 for e in energies)
    keys = [(r.target, r.start) for r in recs]
    assert len(set(keys)) == len(keys)
    assert all(r.target == "t00" for r in recs)  # metal-free: no coordination class
    assert all(r.passed and r.coords is not None for r in recs)
    assert run.totals["passed"] == len(recs)


def test_cancellation_raises_search_cancelled() -> None:
    graph, ref = _butane()
    with pytest.raises(search.SearchCancelled):
        explore.explore(
            graph, None, ref, None, _settings(), _FakeCalc(), map, should_cancel=lambda: True
        )


def test_zero_budget_is_refused() -> None:
    graph, ref = _butane()
    with pytest.raises(search.SearchError):
        explore.explore(graph, None, ref, None, _settings(budget=0), _FakeCalc(), map)


def test_without_hessian_capability_no_hessians_run() -> None:
    graph, ref = _butane()
    calc = _FakeCalc()
    run = explore.explore(graph, None, ref, None, _settings(lowmode_gate=2.0), calc, map)
    assert run.stats["hessians"] == 0
    assert calc.hessian_calls == 0


def test_hessian_capability_enables_low_mode_moves() -> None:
    graph, ref = _butane()
    calc = _FakeCalc(frozenset({CAP_HESSIAN}))
    run = explore.explore(graph, None, ref, None, _settings(lowmode_gate=2.0), calc, map)
    assert run.stats["hessians"] > 0
    assert calc.hessian_calls == run.stats["hessians"]
    assert run.stats["relaxations"] <= 40


def test_fixed_core_is_required_checked_through_hessian_and_release() -> None:
    graph, ref = _butane()
    unsupported = _FakeCalc()
    with pytest.raises(search.SearchError, match="does not declare fixed-atom support"):
        explore.explore(graph, None, ref, None, _settings(freeze_core=(0,)), unsupported, map)
    assert not unsupported.requests

    class FixedCalc(_FakeCalc):
        def __init__(self) -> None:
            super().__init__(frozenset({CAP_FIXED_ATOMS, CAP_HESSIAN}))
            self.hessian_requests: list[RelaxRequest] = []

        def hessian(self, request):
            self.hessian_requests.append(request)
            return super().hessian(request)

    hessian_calc = FixedCalc()
    hessian_run = explore.explore(
        graph,
        None,
        ref,
        None,
        _settings(budget=60, lowmode_gate=2.0, freeze_core=(0,), release=False),
        hessian_calc,
        map,
    )
    assert hessian_calc.hessian_requests
    assert all(q.fixed_atoms[0].index == 0 for q in hessian_calc.hessian_requests)
    assert all(q.fixed_atoms[0].index == 0 for q in hessian_calc.requests)
    assert all(q.coords[0].tolist() == ref[0].tolist() for q in hessian_calc.requests)
    assert hessian_run.stats["fixed_core"][
        "output_displacement_tolerance_angstrom"
    ] == pytest.approx(1e-5)

    graph, spec, ref = _pt_toy()
    release_calc = FixedCalc()
    explore.explore(
        graph,
        spec,
        ref,
        _SHAPE,
        _settings(budget=60, freeze_core=(0,), release=True),
        release_calc,
        map,
    )
    assert any(q.key.startswith("release/") for q in release_calc.requests)
    assert all(q.fixed_atoms[0].index == 0 for q in release_calc.requests)


def test_search_rejects_a_backend_that_claims_fixed_support_but_moves_the_core() -> None:
    graph, ref = _butane()

    class DriftingCalc(_FakeCalc):
        def __init__(self) -> None:
            super().__init__(frozenset({CAP_FIXED_ATOMS}))

        def relax_many(self, requests):
            outcomes = super().relax_many(requests)
            return [
                RelaxOutcome(
                    coords=outcome.coords
                    + np.array([[1e-3, 0.0, 0.0]] + [[0.0, 0.0, 0.0]] * (len(ref) - 1)),
                    energy_eh=outcome.energy_eh,
                    converged=True,
                )
                for outcome in outcomes
            ]

    run = explore.explore(
        graph, None, ref, None, _settings(budget=1, freeze_core=(0,)), DriftingCalc(), map
    )
    assert run.stats["failures"]["core_displacement"] == 1
    assert run.stats["outcome_class"] == "core_displacement"


def test_release_false_publishes_from_restrained_pool() -> None:
    graph, spec, ref = _pt_toy()
    off, on = _FakeCalc(), _FakeCalc()
    run_off = explore.explore(graph, spec, ref, _SHAPE, _settings(release=False), off, map)
    run_on = explore.explore(graph, spec, ref, _SHAPE, _settings(release=True), on, map)
    assert not any(q.key.startswith(("release/", "baseline/")) for q in off.requests)
    assert any(q.key.startswith(("release/", "baseline/")) for q in on.requests)
    assert run_off.records
    assert run_on.records
    assert run_off.stats["relaxations"] <= 40


def test_arrangement_keep_searches_fewer_classes_than_enumerate() -> None:
    graph, spec, ref = _pt_toy()
    kept = explore.explore(
        graph, spec, ref, _SHAPE, _settings(arrangement="keep"), _FakeCalc(), map
    )
    allc = explore.explore(
        graph, spec, ref, _SHAPE, _settings(arrangement="enumerate"), _FakeCalc(), map
    )
    assert len(kept.stats["targets_searched"]) < len(allc.stats["targets_searched"])
    assert len(kept.stats["targets_searched"]) == 1


def test_budget_caps_search_and_release_is_reported() -> None:
    graph, spec, ref = _pt_toy()
    calc = _FakeCalc()
    run = explore.explore(graph, spec, ref, _SHAPE, _settings(budget=40), calc, map)
    keys = [r.key for r in calc.requests]
    released = [k for k in keys if k.startswith(("release/", "baseline/"))]
    assert len(keys) - len(released) <= 40
    assert run.stats["release_relaxations"] == len(released)
    assert run.totals["generated"] == len(keys)


def test_empty_first_rounds_retry_global_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    graph, spec, ref = _pt_toy()
    real, calls = search.generate_starts, []

    def _flaky(*args: Any, **kwargs: Any) -> search.SearchStarts:
        calls.append(1)
        if len(calls) <= 2:  # dg0 and dg1 embed nothing
            return search.SearchStarts()
        return real(*args, **kwargs)

    monkeypatch.setattr(search, "generate_starts", _flaky)
    run = explore.explore(graph, spec, ref, _SHAPE, _settings(budget=40), _FakeCalc(), map)
    assert len(calls) >= 3
    assert run.passing


def test_adaptive_budget_stops_once_saturated() -> None:
    graph, ref = _butane()  # one rotor: a handful of minima, found early
    kw = {"budget": 600, "min_budget": 64, "saturation_window": 64, "batch": 16}
    run = explore.explore(graph, None, ref, None, _settings(adaptive=True, **kw), _FakeCalc(), map)
    assert run.stats["adaptive"] is True
    assert 64 <= run.stats["relaxations"] < 600
    fixed = explore.explore(graph, None, ref, None, _settings(**kw), _FakeCalc(), map)
    assert fixed.stats["adaptive"] is False and fixed.stats["relaxations"] == 600
    assert {round(r.energy_eh, 6) for r in fixed.passing} == {
        round(r.energy_eh, 6) for r in run.passing
    }


def test_adaptive_budget_never_stops_before_its_minimum() -> None:
    graph, ref = _butane()
    kw = {"budget": 600, "min_budget": 200, "saturation_window": 16, "batch": 16}
    run = explore.explore(graph, None, ref, None, _settings(adaptive=True, **kw), _FakeCalc(), map)
    assert run.stats["relaxations"] >= 200


def test_single_moves_bar_other_ring_state_cells(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    real = explore._Explorer._release

    def _spy(self: Any) -> Any:
        seen["ex"] = self
        return real(self)

    monkeypatch.setattr(explore._Explorer, "_release", _spy)
    graph, ref = _butane()
    explore.explore(graph, None, ref, None, _settings(budget=60), _FakeCalc(), map)
    ex = seen["ex"]
    ex.pool.minima = [replace(m, cell="a") for m in ex.pool.minima]
    low, other = ex.pool.ordered()[:2]
    ex.pool.minima[other.id] = replace(other, cell="b")  # the only minimum of a second cell
    ex.marks.clear()
    items = ex._moves("single", 1)
    assert {it[2] for it in items} == {f"single:{low.id}"}  # the lowest is expanded ...
    assert other.id in ex.marks["single"]  # ... and the other cell's best is barred, not expanded
    ex.marks["single"] = {m.id for m in ex.pool.minima}
    assert ex._take("single", 1, cells=True) == []


def test_stop_reason_and_release_status_are_reported() -> None:
    graph, ref = _butane()
    run = explore.explore(graph, None, ref, None, _settings(budget=40), _FakeCalc(), map)
    assert run.stats["stop"] in {"budget", "no_moves"}
    assert run.stats["release"] == "not_needed"  # no reaction pair: nothing to release
    kw = {"budget": 600, "min_budget": 64, "saturation_window": 64, "batch": 16}
    sat = explore.explore(graph, None, ref, None, _settings(adaptive=True, **kw), _FakeCalc(), map)
    assert sat.stats["stop"] == "saturated"
    graph, spec, ref = _pt_toy()
    off = explore.explore(graph, spec, ref, _SHAPE, _settings(release=False), _FakeCalc(), map)
    on = explore.explore(graph, spec, ref, _SHAPE, _settings(), _FakeCalc(), map)
    assert off.stats["release"] == "off"
    assert on.stats["release"] == "done"


class _FailingReleaseCalc(_FakeCalc):
    """Every release relaxation fails to converge."""

    def relax_many(self, requests):
        out = super().relax_many(requests)
        return [
            (
                RelaxOutcome(coords=o.coords, energy_eh=o.energy_eh, converged=False)
                if q.key.startswith(("release/", "baseline/"))
                else o
            )
            for q, o in zip(requests, out)
        ]


def test_failed_release_keeps_restrained_minima_and_says_so() -> None:
    graph, spec, ref = _pt_toy()
    run = explore.explore(graph, spec, ref, _SHAPE, _settings(), _FailingReleaseCalc(), map)
    assert run.stats["release"] == "failed"
    assert run.stats["release_relaxations"] > 0
    assert run.passing


def test_donor_permutations_keep_edge_kinds() -> None:
    # N1-Cl3 is a forming pair and N2-Cl4 a covalent edge: a plain graph swaps (N1 Cl3)<->(N2 Cl4),
    # the typed graph must not, or two distinct coordination classes collapse into one
    graph, spec, ref = _pt_toy()
    edges = graph.edges + (TypedEdge(a=2, b=4, type=EdgeType.COVALENT),)
    typed = replace(graph, edges=edges)
    fake = SimpleNamespace(spec=spec, id=Identity(typed), elements=[a.element for a in typed.atoms])
    assert explore._Explorer._donor_perms(fake) == [(0, 1, 2, 3)]  # type: ignore[arg-type]


def test_dg_starts_go_out_in_parallel_slices(monkeypatch: pytest.MonkeyPatch) -> None:
    graph, spec, ref = _pt_toy()
    calls: list[tuple[int, Any]] = []
    real = search.generate_starts

    def _spy(*args: Any, **kwargs: Any) -> search.SearchStarts:
        calls.append((args[-1].count, args[-1].window))
        return real(*args, **kwargs)

    monkeypatch.setattr(search, "generate_starts", _spy)
    explore.explore(graph, spec, ref, _SHAPE, _settings(starts=10), _FakeCalc(), map)
    # Input uses one slot; the remaining bootstrap starts still use parallel slices.
    assert calls[:3] == [(3, (i, i + 1)) for i in range(3)]


def test_starts_lost_to_timeouts_are_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    graph, spec, ref = _pt_toy()
    monkeypatch.setattr(search, "generate_starts", lambda *a, **k: search.SearchStarts())

    class NoInput(_FakeCalc):
        def relax_many(self, requests):
            self.requests += list(requests)
            return [RelaxOutcome() for _ in requests]

    run = explore.explore(graph, spec, ref, _SHAPE, _settings(budget=40), NoInput(), map)
    assert not run.passing
    assert run.stats["stop"] == "no_minima"
    assert run.stats["failures"]["dg_no_start"] > 0


def test_bootstrap_and_probe_leave_feedback_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    graph, ref = _butane()
    calc = _FakeCalc()
    generated = []

    def dg_task(args):
        generated.append(1)
        return args[4].id, [ref.copy()], None, ()

    def reembed(self, quota):
        return [(f"reembed/test{i}", ref.copy(), "reembed:0") for i in range(quota)]

    monkeypatch.setattr(explore, "_dg_task", dg_task)
    monkeypatch.setattr(explore._Explorer, "_reembed", reembed)
    run = explore.explore(graph, None, ref, None, _settings(budget=16, starts=132), calc)
    keys = [q.key.split("/")[0] for q in calc.requests]
    assert keys.count("input") == 1
    assert keys.count("dg0") == 3
    assert keys.count("probe") <= 4
    assert "single" in keys and "reembed" in keys
    assert len(generated) <= len(calc.requests) <= 16
    assert run.stats["spent"]["single"] > 0


def test_checkpoint_survives_later_batch_cancellation() -> None:
    graph, ref = _butane()
    snapshots = []

    class Interrupted(_FakeCalc):
        def relax_many(self, requests):
            if self.requests:
                raise search.SearchCancelled("backend interrupted")
            return super().relax_many(requests)

    with pytest.raises(search.SearchCancelled):
        explore.explore(
            graph, None, ref, None, _settings(), Interrupted(), checkpoint=snapshots.append
        )
    assert len(snapshots) == 1
    assert snapshots[0].records
    assert snapshots[0].stats["relaxations"] == 1
    assert snapshots[0].stats["checkpoint_stage"] == "restrained"
    assert snapshots[0].stats["release"] == "pending"
    assert all(r.passed for r in snapshots[0].records)


def test_soft_wall_budget_trims_batches_and_returns_audited_results(monkeypatch) -> None:
    graph, ref = _butane()
    clock = [0.0]
    snapshots = []
    monkeypatch.setattr(explore.time, "monotonic", lambda: clock[0])

    class Slow(_FakeCalc):
        def relax_many(self, requests):
            clock[0] += 10 * len(requests)
            return super().relax_many(requests)

    calc = Slow()
    run = explore.explore(
        graph,
        None,
        ref,
        None,
        _settings(budget=48, time_limit_seconds=95),
        calc,
        checkpoint=snapshots.append,
    )
    assert run.stats["stop"] == "time_limit"
    assert clock[0] < 95
    assert run.records and snapshots
    assert len(calc.requests) < 10
    assert any(q.key.startswith("single/") for q in calc.requests)


@pytest.mark.parametrize("limit", [0, -1, float("nan"), float("inf")])
def test_invalid_time_limit_is_refused(limit) -> None:
    graph, ref = _butane()
    with pytest.raises(search.SearchError):
        explore.explore(graph, None, ref, None, _settings(time_limit_seconds=limit), _FakeCalc())


def test_count_budget_with_ample_wall_budget_still_releases(monkeypatch) -> None:
    graph, spec, ref = _pt_toy()
    clock = [0.0]
    monkeypatch.setattr(explore.time, "monotonic", lambda: clock[0])

    class Timed(_FakeCalc):
        def relax_many(self, requests):
            clock[0] += len(requests)
            return super().relax_many(requests)

    calc = Timed()
    run = explore.explore(
        graph, spec, ref, _SHAPE, _settings(budget=8, time_limit_seconds=10000), calc
    )
    assert run.stats["stop"] == "budget"
    assert run.stats["release"] == "done"
    assert any(q.key.startswith(("release/", "baseline/")) for q in calc.requests)


def test_failed_release_checkpoint_preserves_audited_restrained_minima() -> None:
    graph, spec, ref = _pt_toy()
    snapshots = []
    explore.explore(
        graph,
        spec,
        ref,
        _SHAPE,
        _settings(budget=8),
        _FailingReleaseCalc(),
        checkpoint=snapshots.append,
    )
    assert snapshots[-1].records
    assert snapshots[-1].stats["checkpoint_stage"] == "restrained"


def test_budget_one_optimizes_exact_input_and_checkpoints_without_dg(monkeypatch) -> None:
    graph, ref = _butane()
    calc = _FakeCalc()
    snapshots = []

    def no_dg(args):
        pytest.fail("budget one must optimize input without generating DG starts")

    monkeypatch.setattr(explore, "_dg_task", no_dg)
    run = explore.explore(
        graph, None, ref, None, _settings(budget=1), calc, checkpoint=snapshots.append
    )
    assert len(calc.requests) == 1
    assert calc.requests[0].key == "input/reference"
    np.testing.assert_array_equal(calc.requests[0].coords, ref)
    assert len(run.records) == 1
    np.testing.assert_array_equal(run.records[0].coords, ref)
    assert run.records[0].energy_eh == _energy(ref)
    assert run.stats["relaxations"] == 1
    assert run.stats["input_seed"]["published"] is True
    assert snapshots[0].stats["input_seed"]["status"] == "accepted"
    assert snapshots[0].stats["input_seed"]["minimum_id"] == 0


def test_lower_input_basin_survives_higher_dg_and_final_release() -> None:
    graph, spec, ref = _pt_toy()

    class LowInput(_FakeCalc):
        def relax_many(self, requests):
            outs = super().relax_many(requests)
            return [
                replace(
                    out,
                    energy_eh=(
                        -101.0
                        if q.key == "input/reference"
                        or (
                            q.key.startswith(("release/", "baseline/"))
                            and np.array_equal(q.coords, ref)
                        )
                        else -100.0
                    ),
                )
                for q, out in zip(requests, outs)
            ]

    calc = LowInput()
    run = explore.explore(graph, spec, ref, _SHAPE, _settings(budget=8), calc)
    assert calc.requests[0].key == "input/reference"
    assert any(q.key.startswith("dg0/") for q in calc.requests)
    assert any(q.key.startswith(("release/", "baseline/")) for q in calc.requests)
    assert len(run.records) == 1 and run.records[0].energy_eh == -101.0
    np.testing.assert_array_equal(run.records[0].coords, ref)
    seed = run.stats["input_seed"]
    assert seed["release_requested"] is True
    assert seed["final_stage"] == "released"
    assert seed["final_disposition"] == "published"
    assert seed["final_minimum_id"] == run.records[0].start


def test_raw_input_audit_failure_skips_input_and_falls_back_to_dg(monkeypatch) -> None:
    graph, ref = _butane()
    real = search.audit_structure

    def reject_raw(coords, graph, spec, reference, *args, **kw):
        if coords is reference:
            return search.AuditRecord(failed_checks=("contacts",))
        return real(coords, graph, spec, reference, *args, **kw)

    monkeypatch.setattr(search, "audit_structure", reject_raw)
    calc = _FakeCalc()
    run = explore.explore(graph, None, ref, None, _settings(budget=4), calc)
    assert all(q.key != "input/reference" for q in calc.requests)
    assert calc.requests and calc.requests[0].key.startswith("dg0/")
    assert run.records
    assert run.stats["input_seed"]["status"] == "raw_rejected"
    assert run.stats["input_seed"]["raw_failed_checks"] == ["contacts"]


def test_unconverged_input_is_not_inserted_and_dg_can_recover() -> None:
    graph, ref = _butane()

    class UnconvergedInput(_FakeCalc):
        def relax_many(self, requests):
            outs = super().relax_many(requests)
            return [
                (
                    replace(out, converged=False, energy_eh=-1000.0)
                    if q.key == "input/reference"
                    else out
                )
                for q, out in zip(requests, outs)
            ]

    run = explore.explore(graph, None, ref, None, _settings(budget=4), UnconvergedInput())
    assert run.records and all(r.energy_eh > -1000.0 for r in run.records)
    assert run.stats["relaxations"] == 4
    assert run.stats["input_seed"]["status"] == "optimization_rejected"
    assert run.stats["input_seed"]["optimization_failed_checks"] == ["converged"]


def test_input_stereocentres_are_checked_before_any_dg() -> None:
    _, elements, ref = _embed("C[C@H](O)F")
    graph, _, _ = build_topology(elements, ref, (None, [], [], [], 1.25), None)

    class Mirror(_FakeCalc):
        def relax_many(self, requests):
            outs = super().relax_many(requests)
            return [replace(out, coords=out.coords * np.array([-1.0, 1.0, 1.0])) for out in outs]

    calc = Mirror()
    run = explore.explore(graph, None, ref, None, _settings(budget=1), calc)
    assert [q.key for q in calc.requests] == ["input/reference"]
    assert not run.records
    assert run.stats["input_seed"]["optimization_failed_checks"] == ["stereo"]


def test_input_can_be_excluded_by_a_lower_energy_search_minimum() -> None:
    graph, ref = _butane()

    class HighInput(_FakeCalc):
        def relax_many(self, requests):
            outs = super().relax_many(requests)
            return [
                replace(out, energy_eh=-99.0 if q.key == "input/reference" else -100.0)
                for q, out in zip(requests, outs)
            ]

    run = explore.explore(graph, None, ref, None, _settings(budget=8), HighInput())
    assert run.records and run.records[0].energy_eh == -100.0
    assert run.stats["input_seed"]["status"] == "accepted"
    assert run.stats["input_seed"]["published"] is False
    assert run.stats["input_seed"]["final_disposition"] == "outside_energy_window"


def test_enumerated_named_shape_inputs_use_actual_class_only() -> None:
    graph, spec, ref = _pt_toy()
    calc = _FakeCalc()
    run = explore.explore(
        graph, spec, ref, _SHAPE, _settings(budget=1, arrangement="enumerate", release=False), calc
    )
    assert [q.key for q in calc.requests] == ["input/reference"]
    assert len(run.records) == 1
    allowed = {target.command for target in run.targets}
    rec = search.audit_structure(ref, graph, spec, ref, _SHAPE, allowed=allowed)
    actual_ids = {target.id for target in run.targets if target.command == rec.actual_class}
    assert run.records[0].target in actual_ids
    assert run.stats["input_seed"]["status"] == "accepted"


def test_raw_input_audit_does_not_require_optimized_restraint_residual(monkeypatch) -> None:
    from confflow.science.confgen.calculator import DistanceRestraint

    graph, ref = _butane()
    # A target can differ from a valid raw distance (as with symmetry-averaged restraints).
    target = float(np.linalg.norm(ref[0] - ref[1])) + 0.1
    monkeypatch.setattr(
        explore._Explorer, "_restraints", lambda self: (DistanceRestraint(0, 1, target),)
    )
    calc = _FakeCalc()
    run = explore.explore(graph, None, ref, None, _settings(budget=1), calc)
    assert [q.key for q in calc.requests] == ["input/reference"]
    assert run.stats["input_seed"]["raw_failed_checks"] == []
    # The fake optimizer did not satisfy the target, so its optimized result is rejected.
    assert run.stats["input_seed"]["optimization_failed_checks"] == ["restraint"]
    assert not run.records


class _SizeCalc(_FakeCalc):
    """Fake engine that records the size of every relaxation call."""

    def __init__(self) -> None:
        super().__init__()
        self.sizes: list[int] = []

    def relax_many(self, requests):
        self.sizes.append(len(requests))
        return super().relax_many(requests)


@pytest.mark.parametrize("round_batches", [1, 3])
def test_rounds_are_filled_with_dg_starts_when_moves_are_missing(round_batches: int) -> None:
    graph, spec, ref = _pt_toy()  # no rotors: single and low-mode moves offer nothing
    calc = _SizeCalc()
    st = _settings(budget=60, batch=64, relaxation_batch=4, round_batches=round_batches,
                   release=False)  # fmt: skip
    run = explore.explore(graph, spec, ref, _SHAPE, st, calc, map)
    assert run.stats["round_batches"] == round_batches
    rounds = calc.sizes[2:]  # after the input relaxation and the bootstrap DG starts
    assert rounds and all(n == 4 * round_batches for n in rounds[:-1])  # the last one: budget
    assert sum(calc.sizes) == 60


def test_round_batches_must_be_positive() -> None:
    graph, spec, ref = _pt_toy()
    with pytest.raises(search.SearchError, match="batch sizes"):
        explore.explore(graph, spec, ref, _SHAPE, _settings(round_batches=0), _FakeCalc(), map)
