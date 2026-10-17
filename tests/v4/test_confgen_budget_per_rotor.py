"""Adaptive budget cap scales with the rotor count (``budget_per_rotor``).

The cap is ``max(budget, budget_per_rotor * rotors)`` for adaptive searches and ``budget``
otherwise; the fake engine of ``test_confgen_explore`` keeps the relaxations cheap.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from confflow.execution import confgen_search_worker as worker
from confflow.science.confgen import explore, search
from confflow.science.confgen.search_spec import (
    DEFAULT_SEARCH_BUDGET_CAP,
    DEFAULT_SEARCH_BUDGET_PER_ROTOR,
)
from tests.v4._dg_search_helpers import _embed, _write_fake, build_topology
from tests.v4.test_confgen_explore import _FakeCalc, _settings
from tests.v4.test_confgen_search_step import _driving_metal, _native, _run
from tests.v4.test_confgen_search_worker import _job, _metal_parts


def _explorer(smiles: str, **kw) -> explore._Explorer:
    _, els, ref = _embed(smiles)
    graph, _, _ = build_topology(els, ref, (None, [], [], [], 1.25), None)
    st = _settings(**kw)
    ref = np.asarray(ref, dtype=float)
    return explore._Explorer(graph, None, ref, None, st, _FakeCalc(), map, None)


def _graph_ref(smiles: str):
    _, els, ref = _embed(smiles)
    graph, _, _ = build_topology(els, ref, (None, [], [], [], 1.25), None)
    return graph, ref


def test_adaptive_cap_scales_with_rotors() -> None:
    ex = _explorer("C" * 17, adaptive=True, budget=DEFAULT_SEARCH_BUDGET_CAP,
                   budget_per_rotor=DEFAULT_SEARCH_BUDGET_PER_ROTOR)  # fmt: skip
    assert len(ex.rotors) == 14
    assert ex.cap == 14000
    assert ex._left() == 14000


def test_adaptive_cap_keeps_floor_for_few_rotors() -> None:
    ex = _explorer("C" * 6, adaptive=True, budget=DEFAULT_SEARCH_BUDGET_CAP,
                   budget_per_rotor=DEFAULT_SEARCH_BUDGET_PER_ROTOR)  # fmt: skip
    assert len(ex.rotors) == 3
    assert ex.cap == DEFAULT_SEARCH_BUDGET_CAP == 8000


def test_budget_per_rotor_ignored_without_adaptive() -> None:
    ex = _explorer("C" * 17, adaptive=False, budget=40, budget_per_rotor=1000)
    assert ex.cap == 40
    graph, ref = _graph_ref("C" * 17)
    run = explore.explore(graph, None, ref, None, _settings(budget=40, budget_per_rotor=1000),
                          _FakeCalc(), map)  # fmt: skip
    assert run.stats["budget_cap"] == 40
    assert run.stats["relaxations"] <= 40


def test_explore_reports_effective_cap() -> None:
    graph, ref = _graph_ref("C" * 6)
    kw = {"budget": 40, "min_budget": 16, "budget_per_rotor": 5, "batch": 16}
    run = explore.explore(graph, None, ref, None, _settings(adaptive=True, **kw), _FakeCalc(), map)
    assert run.stats["budget_cap"] == max(40, 5 * run.stats["rotors"])
    assert run.stats["relaxations"] <= run.stats["budget_cap"]


def test_negative_budget_per_rotor_rejected() -> None:
    graph, ref = _graph_ref("CCCC")
    with pytest.raises(search.SearchError, match="budget_per_rotor"):
        explore.explore(graph, None, ref, None, _settings(budget_per_rotor=-1), _FakeCalc(), map)


@pytest.mark.parametrize("value", [-1, "1000"])
def test_worker_rejects_bad_budget_per_rotor(tmp_path: Path, value: object) -> None:
    job, _, _, _ = _job(
        tmp_path, *_metal_parts(tmp_path), settings_over={"budget_per_rotor": value}
    )
    with pytest.raises(worker._JobError, match="budget_per_rotor"):
        worker._load_job(job)


def test_worker_budget_per_rotor_defaults_and_passes_through(tmp_path: Path) -> None:
    job, _, _, _ = _job(tmp_path, *_metal_parts(tmp_path, "a"))
    assert worker._load_job(job)["settings"].budget_per_rotor == 0
    job, _, _, _ = _job(
        tmp_path, *_metal_parts(tmp_path, "b"), settings_over={"budget_per_rotor": 1000}
    )
    assert worker._load_job(job)["settings"].budget_per_rotor == 1000


def test_run_layer_writes_budget_per_rotor(tmp_path: Path) -> None:
    _run(_native(search={}), _driving_metal(), _write_fake(tmp_path), tmp_path)
    jobs = list(tmp_path.rglob("search_job.json"))
    assert len(jobs) == 1
    settings = json.loads(jobs[0].read_text())["settings"]
    assert settings["adaptive"] is True
    assert settings["budget_per_rotor"] == DEFAULT_SEARCH_BUDGET_PER_ROTOR


def test_run_layer_fixed_budget_omits_budget_per_rotor(tmp_path: Path) -> None:
    _run(_native(search={"budget": 12}), _driving_metal(), _write_fake(tmp_path), tmp_path)
    jobs = list(tmp_path.rglob("search_job.json"))
    assert len(jobs) == 1
    assert "budget_per_rotor" not in json.loads(jobs[0].read_text())["settings"]
