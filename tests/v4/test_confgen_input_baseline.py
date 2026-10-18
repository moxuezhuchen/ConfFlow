"""Independent input baselines survive search windows, representative changes and interruption."""

from dataclasses import replace

import numpy as np
import pytest

from confflow.science.confgen import explore, search
from confflow.science.confgen.calculator import CalculatorInfo, RelaxOutcome
from confflow.science.confgen.identity import HARTREE_TO_KCAL
from tests.v4.test_confgen_explore import _butane, _pt_toy, _settings


class BaselineCalculator:
    """Controlled energies model different rankings in search and final constraint states."""

    info = CalculatorInfo("fake", "baseline-test")

    def __init__(
        self,
        reference,
        *,
        search_energy=-99.0,
        dg_energy=-100.0,
        baseline_energy=-101.0,
        released_energy=-100.0,
        search_shift=0.0,
        failure=None,
    ):
        self.reference = reference
        self.search_energy = search_energy
        self.dg_energy = dg_energy
        self.baseline_energy = baseline_energy
        self.released_energy = released_energy
        self.search_shift = search_shift
        self.failure = failure
        self.requests = []

    def relax_many(self, requests):
        self.requests.extend(requests)
        outs = []
        for request in requests:
            xyz = request.coords.copy()
            if request.key == "input/reference":
                xyz[-1, 2] += self.search_shift
                energy = self.search_energy
            elif request.key == "baseline/input":
                np.testing.assert_array_equal(request.coords, self.reference)
                if self.failure == "cancel":
                    raise search.SearchCancelled("baseline backend interrupted")
                if self.failure == "missing":
                    outs.append(RelaxOutcome())
                    continue
                if self.failure == "audit":
                    outs.append(RelaxOutcome(xyz, self.baseline_energy, False))
                    continue
                energy = self.baseline_energy
            elif request.key.startswith("release/"):
                energy = self.released_energy
            else:
                energy = self.dg_energy
            outs.append(RelaxOutcome(xyz, energy, True))
        return outs


def _run(monkeypatch, *, budget=2, snapshots=None, **calc_kw):
    graph, spec, ref = _pt_toy()
    dg = ref.copy()
    dg[-1, 2] += 0.01
    monkeypatch.setattr(explore, "_dg_task", lambda args: (args[4].id, [dg.copy()], None, ()))
    calc = BaselineCalculator(ref, **calc_kw)
    run = explore.explore(
        graph,
        spec,
        ref,
        "square_planar",
        _settings(budget=budget, probes=0, share_dg=1, share_single=0, share_reembed=0),
        calc,
        checkpoint=snapshots.append if snapshots is not None else None,
    )
    return run, calc, ref


def test_final_input_baseline_runs_outside_search_window_and_can_be_lowest(monkeypatch):
    snapshots = []
    run, calc, ref = _run(monkeypatch, snapshots=snapshots)
    baseline = run.input_baseline
    assert baseline.status == "released" and baseline.comparable_to_final
    assert baseline.search_record.energy_eh == -99.0
    assert baseline.final_record.energy_eh == -101.0
    assert run.stats["input_seed"]["in_search_window"] is False
    assert run.records[0].energy_eh <= baseline.final_record.energy_eh
    assert baseline.lowest_final_delta_kcal <= 0
    assert baseline.published
    assert [q.key for q in calc.requests][2] == "baseline/input"
    np.testing.assert_array_equal(baseline.final_record.coords, ref)
    assert calc.requests[2].restraints == baseline.final_restraints
    assert run.stats["relaxations"] == 2
    assert run.stats["release_relaxations"] == 2
    assert run.stats["baseline_release_relaxations"] == 1
    assert run.totals["generated"] == len(calc.requests) == 4
    assert snapshots[0].input_baseline.search_record is not None
    assert snapshots[0].input_baseline.final_record is None
    data = run.summary()["input_baseline"]
    assert data["final_record"]["coordinates"] == ref.tolist()
    assert data["final_origin"] == "baseline/input"


def test_high_final_baseline_is_archived_even_when_not_published(monkeypatch):
    run, _, _ = _run(monkeypatch, baseline_energy=-99.0)
    baseline = run.input_baseline
    assert baseline.final_record.energy_eh == -99.0
    assert baseline.comparable_to_final and baseline.published is False
    assert baseline.disposition == "outside_energy_window"
    assert run.records[0].energy_eh == -100.0
    assert baseline.lowest_final_delta_kcal == pytest.approx(-HARTREE_TO_KCAL)
    assert baseline.search_record.energy_eh == -99.0


def test_pool_and_release_dedup_do_not_overwrite_independent_baselines(monkeypatch):
    delta = 0.1 / HARTREE_TO_KCAL
    run, calc, ref = _run(
        monkeypatch,
        search_energy=-100.0,
        dg_energy=-100.0 - delta,
        baseline_energy=-101.0,
        released_energy=-101.0 - delta,
    )
    baseline = run.input_baseline
    assert baseline.search_record.energy_eh == -100.0
    assert baseline.final_record.energy_eh == -101.0
    np.testing.assert_array_equal(baseline.search_record.coords, ref)
    np.testing.assert_array_equal(baseline.final_record.coords, ref)
    assert not baseline.search_record.coords.flags.writeable
    assert not baseline.final_record.coords.flags.writeable
    assert len(run.records) == 1
    assert run.records[0].energy_eh == -101.0 - delta
    assert not np.array_equal(run.records[0].coords, baseline.final_record.coords)
    assert baseline.published and baseline.final_pool_id == run.records[0].start
    assert baseline.lowest_final_delta_kcal == pytest.approx(-0.1)
    assert sum(q.key == "baseline/input" for q in calc.requests) == 1


def test_identical_original_input_is_not_released_twice(monkeypatch):
    run, calc, _ = _run(monkeypatch, budget=1)
    assert [q.key for q in calc.requests] == ["input/reference", "baseline/input"]
    assert run.stats["release_relaxations"] == run.stats["baseline_release_relaxations"] == 1


def test_changed_search_input_keeps_its_distinct_release_path(monkeypatch):
    run, calc, ref = _run(monkeypatch, budget=1, search_shift=0.01)
    assert [q.key for q in calc.requests] == ["input/reference", "baseline/input", "release/m00000"]
    np.testing.assert_array_equal(calc.requests[1].coords, ref)
    assert not np.array_equal(calc.requests[2].coords, ref)
    assert not np.array_equal(run.input_baseline.search_record.coords, ref)
    assert run.stats["release_relaxations"] == 2
    assert run.input_baseline.release_relaxations == 1


@pytest.mark.parametrize(
    "failure, checks", [("missing", ("not_converged",)), ("audit", ("converged",))]
)
def test_failed_final_baseline_preserves_search_evidence_without_comparison(
    monkeypatch, failure, checks
):
    snapshots = []
    run, _, _ = _run(monkeypatch, failure=failure, snapshots=snapshots)
    baseline = run.input_baseline
    assert baseline.status == "release_failed"
    assert baseline.failed_checks == checks
    assert baseline.search_record is not None and baseline.final_record is None
    assert baseline.comparable_to_final is False
    assert baseline.lowest_final_delta_kcal is None
    assert baseline.release_relaxations == 1
    assert run.records  # Ordinary candidates can still produce a valid final set.
    assert snapshots[-1].input_baseline.search_record is not None


def test_baseline_cancellation_checkpoint_preserves_search_geometry(monkeypatch):
    graph, spec, ref = _pt_toy()
    calc = BaselineCalculator(ref, failure="cancel")
    snapshots = []
    with pytest.raises(search.SearchCancelled):
        explore.explore(
            graph,
            spec,
            ref,
            "square_planar",
            _settings(budget=1),
            calc,
            checkpoint=snapshots.append,
        )
    baseline = snapshots[-1].input_baseline
    assert baseline.status == "release_cancelled"
    np.testing.assert_array_equal(baseline.search_record.coords, ref)
    assert baseline.final_record is None and not baseline.comparable_to_final
    assert snapshots[-1].records


def test_wall_limit_defers_baseline_but_retains_completed_search(monkeypatch):
    graph, spec, ref = _pt_toy()
    clock = [0.0]
    monkeypatch.setattr(explore.time, "monotonic", lambda: clock[0])

    class Slow(BaselineCalculator):
        def relax_many(self, requests):
            clock[0] += 10 * len(requests)
            return super().relax_many(requests)

    calc = Slow(ref)
    run = explore.explore(
        graph, spec, ref, "square_planar", _settings(budget=1, time_limit_seconds=15), calc
    )
    assert [q.key for q in calc.requests] == ["input/reference"]
    assert run.input_baseline.status == "release_deferred"
    assert run.input_baseline.search_record is not None
    assert run.input_baseline.final_record is None
    assert not run.input_baseline.comparable_to_final
    assert run.input_baseline.release_relaxations == 0


@pytest.mark.parametrize("metal, release", [(True, False), (False, True)])
def test_same_constraint_state_reuses_search_baseline_without_extra_calls(metal, release):
    if metal:
        graph, spec, ref = _pt_toy()
        shape = "square_planar"
    else:
        graph, ref = _butane()
        spec, shape = None, None
    calc = BaselineCalculator(ref)
    run = explore.explore(graph, spec, ref, shape, _settings(budget=1, release=release), calc)
    baseline = run.input_baseline
    assert baseline.status == "reused" and baseline.comparable_to_final
    assert baseline.final_record.energy_eh == baseline.search_record.energy_eh
    assert baseline.final_restraints == baseline.search_restraints
    assert baseline.final_origin == "input/reference"
    assert baseline.release_relaxations == 0
    assert len(calc.requests) == 1


def test_baseline_record_owns_its_coordinate_copy():
    coords = np.zeros((2, 3))
    record = search.SearchRecord("t00", 0, False, -1.0, True, coords=coords)
    baseline = search.InputBaseline(search_record=record)
    coords[:] = 5.0
    np.testing.assert_array_equal(baseline.search_record.coords, np.zeros((2, 3)))
    newer = replace(baseline, status="released", final_record=record)
    assert newer.search_record.coords is not baseline.search_record.coords
    assert baseline.status == "pending"


def test_failed_input_audit_evidence_survives_later_dg_batches(monkeypatch):
    graph, spec, ref = _pt_toy()
    monkeypatch.setattr(explore, "_dg_task", lambda args: (args[4].id, [ref.copy()], None, ()))

    class BadInput(BaselineCalculator):
        def relax_many(self, requests):
            outs = super().relax_many(requests)
            return [
                replace(out, converged=False) if request.key == "input/reference" else out
                for request, out in zip(requests, outs)
            ]

    run = explore.explore(graph, spec, ref, "square_planar", _settings(budget=4), BadInput(ref))
    assert run.records
    assert run.input_baseline.status == "optimization_rejected"
    assert run.input_baseline.failed_checks == ("converged",)
    assert run.input_baseline.search_record is None
    assert run.input_baseline.final_record is None
    assert run.input_baseline.release_relaxations == 0


def test_budget_expires_before_input_optimization_keeps_no_energy(monkeypatch):
    graph, spec, ref = _pt_toy()
    calc = BaselineCalculator(ref)
    run = explore.explore(
        graph, spec, ref, "square_planar", _settings(budget=1, time_limit_seconds=0.01), calc
    )
    assert not calc.requests
    assert run.input_baseline.status == "search_deferred"
    assert run.input_baseline.search_record is None
    assert run.input_baseline.final_record is None


def test_raw_input_stereo_perception_failure_is_preserved(monkeypatch):
    graph, ref = _butane()

    def cannot_perceive(*args):
        raise ValueError("cannot establish input stereochemistry")

    monkeypatch.setattr(explore, "perceive_mol", cannot_perceive)
    calc = BaselineCalculator(ref)
    run = explore.explore(graph, None, ref, None, _settings(budget=1), calc)
    assert run.input_baseline.status == "raw_rejected"
    assert run.input_baseline.raw_failed_checks == ("stereo_perception",)
    assert all(request.key != "input/reference" for request in calc.requests)
