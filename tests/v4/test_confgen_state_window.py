"""Per-state energy window: grouping and selection, the explorer, the record label and the report.

The explorer runs on the pentane reference with the fake engine of the explorer tests. The state
scan is replaced where a deterministic flip signature is needed (the real scan needs a torsion
that really flips, which the fake engine does not produce).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from confflow.domain.diagnostics import DiagnosticSeverity
from confflow.execution import confgen_search_run as search_run
from confflow.science.confgen import explore, search, state_scan
from confflow.science.confgen.identity import HARTREE_TO_KCAL
from tests.v4.test_confgen_explore import _FakeCalc, _settings
from tests.v4.test_confgen_fixed_states import _pentane_ref


def _eh(kcal: float) -> float:
    return kcal / HARTREE_TO_KCAL


# ----------------------------------------------------------------------------- 1. selection


def _entries() -> list[tuple[int, float, frozenset[int]]]:
    return [
        (1, _eh(0.0), frozenset()),  # input state at the pool lowest
        (2, _eh(1.0), frozenset({1})),  # state A, lowest 1.0
        (3, _eh(1.5), frozenset({1})),
        (4, _eh(3.0), frozenset({1})),  # third member of A: beyond state_max=2
        (5, _eh(20.0), frozenset({2})),  # state B: outside the 12 kcal window
        (6, _eh(5.0), frozenset({3})),  # state C
        (7, _eh(6.0), frozenset({3})),
        (8, _eh(7.0), frozenset({3})),
    ]


def test_selection_takes_lowest_members_of_each_state_inside_the_window() -> None:
    pick = explore.select_state_minima(_entries(), 12.0, 2, 12)
    assert pick["ids"] == [2, 3, 6, 7]
    assert pick["truncated"] == 0
    assert [s["signature"] for s in pick["states"]] == [[1], [3]]
    assert pick["states"][0]["lowest_kcal"] == pytest.approx(1.0)
    assert pick["states"][0]["minima"] == 3
    assert pick["states"][1]["lowest_kcal"] == pytest.approx(5.0)


def test_input_state_is_never_selected() -> None:
    entries = [(1, _eh(0.0), frozenset()), (2, _eh(0.5), frozenset())]
    assert explore.select_state_minima(entries, 12.0, 2, 12) == {
        "ids": [],
        "states": [],
        "truncated": 0,
    }


def test_state_outside_the_window_is_not_selected_relative_to_the_pool_lowest() -> None:
    entries = [(1, _eh(0.0), frozenset({1})), (2, _eh(12.5), frozenset({2}))]
    pick = explore.select_state_minima(entries, 12.0, 2, 12)
    assert pick["ids"] == [1]  # the lowest is itself in state 1; state 2 is 12.5 kcal up


def test_state_max_states_cuts_the_highest_states_and_counts_them() -> None:
    pick = explore.select_state_minima(_entries(), 12.0, 2, 1)
    assert pick["ids"] == [2, 3]  # state A (1.0 kcal) kept, state C (5.0 kcal) cut
    assert pick["truncated"] == 1
    assert [s["signature"] for s in pick["states"]] == [[1]]


def test_empty_pool_selects_nothing() -> None:
    assert explore.select_state_minima([], 12.0, 2, 12) == {
        "ids": [],
        "states": [],
        "truncated": 0,
    }


def test_settings_defaults_keep_the_window_off() -> None:
    settings = explore.ExploreSettings()
    assert settings.state_window_kcal is None
    assert settings.state_max == 2 and settings.state_max_states == 12


# ----------------------------------------------------------------------------- 2. record label


def test_search_record_kept_by_is_serialised_only_when_set() -> None:
    base = search.SearchRecord("t", 0, True, -1.0, True)
    assert "kept_by" not in base.to_dict()
    tagged = search.SearchRecord("t", 0, True, -1.0, True, kept_by="state")
    assert tagged.to_dict()["kept_by"] == "state"


# ----------------------------------------------------------------------------- 3. explorer


def test_window_off_adds_no_key_and_no_label() -> None:
    graph, ref = _pentane_ref()
    run = explore.explore(graph, None, ref, None, _settings(budget=40), _FakeCalc(), map)
    assert "state_window" not in run.stats
    assert "state_changes" in run.stats
    assert run.records and all(r.kept_by is None for r in run.records)
    assert all("kept_by" not in r.to_dict() for r in run.records)


def _fake_scan(top: float) -> Any:
    """Return a scan that flags the highest-energy share ``top`` of the pool as one state."""

    def scan(identity, reference, coords, energies, declared=()):  # noqa: ARG001
        count = len(energies)
        order = np.argsort(np.asarray(energies, dtype=float))
        flagged = sorted(int(i) for i in order[count - max(1, int(count * top)) :])
        if count < 2 or not flagged:
            return {"aligned": True, "scanned": count, "units": []}
        unit = {
            "structures": len(flagged), "elements": 1, "atoms": [0, 1, 2, 3], "kind": "hand",
            "theta0": 60.0, "lowest_changed_kcal": 0.0, "lowest_same_kcal": 0.0,
            "members": flagged,
        }  # fmt: skip
        return {"aligned": True, "scanned": count, "units": [unit]}

    return scan


def test_window_on_publishes_state_minima_outside_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(state_scan, "scan_states", _fake_scan(0.34))
    graph, ref = _pentane_ref()
    settings = _settings(budget=40, window_kcal=0.0001, state_window_kcal=50.0, state_max=2)
    run = explore.explore(graph, None, ref, None, settings, _FakeCalc(), map)
    report = run.stats["state_window"]
    assert report["window_kcal"] == 50.0 and report["state_max"] == 2
    labels = [r.kept_by for r in run.records]
    assert set(labels) <= {"window", "state"} and labels
    lowest = min(r.energy_eh for r in run.records if r.energy_eh is not None)
    state_rows = [r for r in run.records if r.kept_by == "state"]
    assert len(state_rows) == report["added_structures"]
    for row in state_rows:
        assert (row.energy_eh - lowest) * HARTREE_TO_KCAL > 0.0001
        assert row.state_flips  # still in the state after the release
    assert report["added_states"] >= 1 and state_rows


def test_scan_failure_is_reported_and_never_fails_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("scan exploded")

    monkeypatch.setattr(state_scan, "scan_states", boom)
    graph, ref = _pentane_ref()
    settings = _settings(budget=40, state_window_kcal=12.0)
    run = explore.explore(graph, None, ref, None, settings, _FakeCalc(), map)
    assert run.records
    assert run.stats["state_window"]["error"] == "scan exploded"
    assert run.stats["state_window"]["added_structures"] == 0
    assert all(r.kept_by == "window" for r in run.records)


# ----------------------------------------------------------------------------- 4. run module


def test_state_window_diagnostic_lists_only_added_states_one_based() -> None:
    item = SimpleNamespace(id="w", logical_key="k")
    stats = {
        "state_window": {
            "window_kcal": 15.0,
            "state_max": 2,
            "state_max_states": 2,
            "states": [
                {"atoms": [[0, 1, 2, 3]], "lowest_kcal": 5.0, "minima": 3, "added": 0,
                 "released_lowest_kcal": None},  # inside the ordinary window: not listed
                {"atoms": [[26, 27, 29, 30]], "lowest_kcal": 16.0, "minima": 2, "added": 2,
                 "released_lowest_kcal": 15.56},  # selected on restrained energies
                {"atoms": [[1, 2, 3, 4]], "lowest_kcal": 9.0, "minima": 2, "added": 1,
                 "released_lowest_kcal": 9.5},
            ],
            "added_states": 2,
            "states_no_added": 1,
            "added_structures": 3,
            "truncated": 1,
        }
    }  # fmt: skip
    diag = search_run._state_window_diagnostic(stats, "step", item, base=1)
    assert diag is not None and diag.code == "confgen_state_window"
    assert diag.severity is DiagnosticSeverity.INFO
    details = dict(diag.details)
    assert [s["atoms"] for s in details["states"]] == [((2, 3, 4, 5),), ((27, 28, 30, 31),)]
    assert [s["released_lowest_kcal"] for s in details["states"]] == [9.5, 15.56]
    assert details["states"][1]["exceeds_window"] is True
    assert details["states"][0]["exceeds_window"] is False
    assert details["states_already_in_window"] == 1 and details["states_selected"] == 3
    assert details["truncated"] == 1 and details["added_structures"] == 3
    assert "[27, 28, 30, 31]" in diag.message and "[0, 1" not in diag.message
    assert "selected on restrained energies; released lowest 15.56" in diag.message
    assert "1 further state(s) cut" in diag.message
    assert search_run._state_window_diagnostic({}, "step", item) is None
    assert search_run._state_window_diagnostic({"state_window": None}, "step", item) is None
