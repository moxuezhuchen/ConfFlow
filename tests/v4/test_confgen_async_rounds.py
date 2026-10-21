"""Tests for the one-round-lag scheduling in ``confflow.science.confgen.explore``.

When the calculator offers ``submit_many``, ``_Explorer._relax`` waits until every job of the
current feedback round is running, then ``_plan_ahead`` plans the next round from the state before
it and queues it.  ``_settle_planned`` absorbs a queued round when the search stops for any reason
but the time limit; at the time limit queued relaxations are cancelled and running ones dropped.

The fake engine computes deterministic energies from coordinates (the butane torsion energy of
``test_confgen_explore``) on a thread pool, so the threaded path runs for real.  The xtb test uses
the fake xtb executable shared with ``test_confgen_xtb``.
"""

from __future__ import annotations

import threading
import time
from concurrent import futures
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from confflow.execution.confgen_xtb import XtbCalculator
from confflow.science.confgen import explore, search
from confflow.science.confgen.calculator import RelaxOutcome, RelaxRequest
from tests.v4._dg_search_helpers import _write_fake
from tests.v4.test_confgen_explore import _butane, _energy, _FakeCalc, _pt_toy, _settings

_SHAPE = "square_planar"


class _PoolCalc(_FakeCalc):
    """Fake engine with ``submit_many`` on a ``workers``-wide thread pool.

    Counts ``submit_many`` calls and ``relax_many`` calls so tests can tell the two paths apart.
    """

    def __init__(self, workers: int = 2, delay: float = 0.005) -> None:
        super().__init__()
        self.delay = delay
        self.pool = futures.ThreadPoolExecutor(max_workers=workers)
        self.lock = threading.Lock()
        self.submit_batches: list[list[futures.Future[RelaxOutcome]]] = []
        self.submit_keys: list[list[str]] = []
        self.submit_calls = 0
        self.relax_calls = 0
        self.fail_next = False  # the next submitted batch raises instead of relaxing

    def relax_many(self, requests):
        self.relax_calls += 1
        return super().relax_many(requests)

    def _one(self, req: RelaxRequest, doomed: bool = False) -> RelaxOutcome:
        if self.delay:
            time.sleep(self.delay)
        if doomed:
            raise RuntimeError("relaxation crashed")
        xyz = np.asarray(req.coords, dtype=float)
        return RelaxOutcome(coords=xyz.copy(), energy_eh=_energy(xyz), converged=True)

    def submit_many(self, requests):
        reqs = list(requests)
        with self.lock:
            self.submit_calls += 1
            self.requests += reqs
            doomed, self.fail_next = self.fail_next, False
            batch = [self.pool.submit(self._one, q, doomed) for q in reqs]
            self.submit_batches.append(batch)
            self.submit_keys.append([q.key for q in reqs])
        return batch

    def close(self) -> None:
        self.pool.shutdown(wait=True)

    @property
    def all_futures(self) -> list[futures.Future[RelaxOutcome]]:
        return [f for batch in self.submit_batches for f in batch]


class _NoSubmitCalc(_FakeCalc):
    """Same engine without ``submit_many``: every round is synchronous."""


@pytest.fixture
def pool_calc():
    calc = _PoolCalc()
    try:
        yield calc
    finally:
        calc.close()


def _spy_plan_ahead(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    real = explore._Explorer._plan_ahead

    def _spy(self: Any, inflight: int) -> None:
        calls.append(inflight)
        return real(self, inflight)

    monkeypatch.setattr(explore._Explorer, "_plan_ahead", _spy)
    return calls


def _stats(run: search.SearchRun) -> dict[str, Any]:
    assert run.stats is not None
    return run.stats


def _totals(run: search.SearchRun) -> dict[str, int]:
    assert run.totals is not None
    return run.totals


def _published(run: search.SearchRun) -> list[tuple[str, int, float, bytes]]:
    rows = []
    for r in run.records:
        coords = b"" if r.coords is None else np.asarray(r.coords, dtype=float).tobytes()
        assert r.energy_eh is not None
        rows.append((r.target, r.start, float(r.energy_eh), coords))
    return rows


def test_async_rounds_publish_identical_records_across_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph, spec, ref = _pt_toy()
    planned = _spy_plan_ahead(monkeypatch)
    runs, calls = [], []
    for _ in range(2):
        calc = _PoolCalc(workers=3, delay=0.002)
        try:
            runs.append(
                explore.explore(graph, spec, ref, _SHAPE, _settings(budget=60, seed=5), calc, map)
            )
            calls.append(calc.submit_calls)
        finally:
            calc.close()
    assert planned, "the lookahead path must run for this test to mean anything"
    assert calls[0] > 0 and calls[0] == calls[1]
    first, second = runs
    assert first.records, "the fake engine should publish minima"
    assert _published(first) == _published(second)
    assert _stats(first)["relaxations"] == _stats(second)["relaxations"]


def test_async_rounds_respect_budget_and_use_lookahead(
    monkeypatch: pytest.MonkeyPatch, pool_calc: _PoolCalc
) -> None:
    graph, spec, ref = _pt_toy()
    planned = _spy_plan_ahead(monkeypatch)
    run = explore.explore(
        graph, spec, ref, _SHAPE, _settings(budget=40, release=False), pool_calc, map
    )
    assert 0 < _stats(run)["relaxations"] <= 40
    assert _totals(run)["generated"] <= 40
    assert planned, "_plan_ahead must queue a round while the current one drains"
    assert pool_calc.submit_calls > pool_calc.relax_calls  # submit path, not relax_many
    assert pool_calc.relax_calls == 0  # release off: no free relaxation goes through relax_many
    # every relaxation the pool ran is either charged to the budget or was a released one
    done = [f for f in pool_calc.all_futures if not f.cancelled() and f.exception() is None]
    assert len(done) >= _stats(run)["relaxations"]


def test_without_submit_many_every_round_is_synchronous(monkeypatch: pytest.MonkeyPatch) -> None:
    graph, spec, ref = _pt_toy()
    planned = _spy_plan_ahead(monkeypatch)
    calc = _NoSubmitCalc()
    assert not hasattr(calc, "submit_many")
    run = explore.explore(graph, spec, ref, _SHAPE, _settings(budget=40), calc, map)
    assert planned == []
    assert calc.requests
    assert _stats(run)["relaxations"] <= 40
    assert run.records


def test_saturated_search_absorbs_the_queued_round(
    monkeypatch: pytest.MonkeyPatch, pool_calc: _PoolCalc
) -> None:
    graph, ref = _butane()
    absorbed: dict[str, Any] = {}
    real_settle = explore._Explorer._settle_planned

    def _spy_settle(self: Any) -> None:
        absorbed["queued"] = self._planned is not None and bool(self._planned[1])
        absorbed["stop"] = self.stop
        absorbed["before"] = self.nopt
        real_settle(self)
        absorbed["after"] = self.nopt

    monkeypatch.setattr(explore._Explorer, "_settle_planned", _spy_settle)
    kw = {"budget": 600, "min_budget": 64, "saturation_window": 64, "batch": 16}
    run = explore.explore(graph, None, ref, None, _settings(adaptive=True, **kw), pool_calc, map)
    assert _stats(run)["stop"] == "saturated"
    assert _stats(run)["adaptive"] is True
    # no future left pending or running once explore returns
    assert all(f.done() for f in pool_calc.all_futures)
    # the round queued ahead was absorbed, not dropped
    assert absorbed["stop"] == "saturated"
    assert absorbed["queued"] is True
    assert absorbed["after"] > absorbed["before"]
    assert _stats(run)["relaxations"] == absorbed["after"]
    assert _stats(run)["relaxations"] <= 600


@pytest.mark.parametrize("doomed", [False, True], ids=["running", "crashed"])
def test_time_limit_drops_the_queued_round(
    monkeypatch: pytest.MonkeyPatch, pool_calc: _PoolCalc, doomed: bool
) -> None:
    graph, spec, ref = _pt_toy()
    pool_calc.delay = 0.05  # slow jobs: the queued round is still pending when the search stops
    run_keys: list[str] = []
    real_run_round = explore._Explorer._run_round
    queued_keys: list[str] = []
    real_plan_ahead = explore._Explorer._plan_ahead

    def _spy_run_round(self: Any, share: Any, items: Any) -> bool:
        run_keys.extend(key for key, _, _ in items)
        return real_run_round(self, share, items)

    def _cut_after_lookahead(self: Any, inflight: int) -> None:
        # Stop the search the moment a round is queued ahead: the time limit is then hit
        # between two bounded backend calls, with one round queued and not yet absorbed.
        pool_calc.fail_next = doomed  # the queued round is the next batch submitted
        real_plan_ahead(self, inflight)
        if self._planned is not None and self._planned[1] and self._submitted:
            queued_keys.extend(self._submitted.keys())
            self.stop = "time_limit"
            self.st = replace(self.st, budget=self.nopt)

    monkeypatch.setattr(explore._Explorer, "_run_round", _spy_run_round)
    monkeypatch.setattr(explore._Explorer, "_plan_ahead", _cut_after_lookahead)
    run = explore.explore(graph, spec, ref, _SHAPE, _settings(budget=200), pool_calc, map)
    assert _stats(run)["stop"] == "time_limit"
    assert queued_keys, "a round must have been queued ahead before the cut"
    # queued relaxations were cancelled or completed, and none of them entered the pool
    assert all(f.done() for f in pool_calc.all_futures)
    assert not set(queued_keys) & set(run_keys)
    assert run_keys, "the rounds before the cut ran through _run_round"
    assert all(r.target for r in run.records)  # the run still publishes normally


def test_empty_next_round_ends_search_without_queued_work(
    monkeypatch: pytest.MonkeyPatch, pool_calc: _PoolCalc
) -> None:
    graph, spec, ref = _pt_toy()
    monkeypatch.setattr(explore._Explorer, "_plan_round", lambda self: None)
    run = explore.explore(graph, spec, ref, _SHAPE, _settings(budget=40), pool_calc, map)
    assert _stats(run)["stop"] == "no_moves"
    assert all(f.done() for f in pool_calc.all_futures)


def test_xtb_submit_many_matches_relax_many(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("FAKE_XTB_MODE", "FAKE_XTB_COUNT", "FAKE_XTB_RECORD", "FAKE_XTB_PAD"):
        monkeypatch.delenv(name, raising=False)
    exe = _write_fake(tmp_path)
    els = ["H", "H"]
    base = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.74]])
    reqs = [
        RelaxRequest("a", base),
        RelaxRequest("b", base + np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.1]])),
        RelaxRequest("c", base + np.array([[0.0, 0.0, 0.0], [0.05, 0.0, 0.0]])),
    ]

    def _make(sub: str) -> XtbCalculator:
        return XtbCalculator(
            str(exe), tmp_path / sub / "work", els, charge=0, uhf=0, max_cycles=5, cores=2
        )

    asynchronous = _make("async")
    try:
        queued = asynchronous.submit_many(reqs)
        assert len(queued) == len(reqs)
        assert all(isinstance(f, futures.Future) for f in queued)
        got = [f.result(timeout=60) for f in queued]
    finally:
        if asynchronous._pool is not None:
            asynchronous._pool.shutdown(wait=True)
    want = _make("sync").relax_many(reqs)
    assert len(got) == len(want) == len(reqs)
    for g, w in zip(got, want):
        assert g.energy_eh is not None and g.energy_eh == pytest.approx(w.energy_eh, abs=1e-12)
        assert g.coords is not None and w.coords is not None
        np.testing.assert_allclose(g.coords, w.coords, atol=1e-9)
        assert g.converged == w.converged
