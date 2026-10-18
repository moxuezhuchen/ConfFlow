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


def test_release_false_publishes_from_restrained_pool() -> None:
    graph, spec, ref = _pt_toy()
    off, on = _FakeCalc(), _FakeCalc()
    run_off = explore.explore(graph, spec, ref, _SHAPE, _settings(release=False), off, map)
    run_on = explore.explore(graph, spec, ref, _SHAPE, _settings(release=True), on, map)
    assert not any(q.key.startswith("release/") for q in off.requests)
    assert any(q.key.startswith("release/") for q in on.requests)
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
    released = [k for k in keys if k.startswith("release/")]
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
                if q.key.startswith("release/")
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
    # The requested ten starts are bounded to a small bootstrap batch.
    assert calls[:4] == [(4, (i, i + 1)) for i in range(4)]


def test_starts_lost_to_timeouts_are_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    graph, spec, ref = _pt_toy()
    monkeypatch.setattr(search, "generate_starts", lambda *a, **k: search.SearchStarts())
    run = explore.explore(graph, spec, ref, _SHAPE, _settings(budget=40), _FakeCalc(), map)
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
    assert keys.count("dg0") == 4
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
    assert snapshots[0].stats["relaxations"] == 4
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
    assert any(q.key.startswith("release/") for q in calc.requests)


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
