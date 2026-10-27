#!/usr/bin/env python3

"""Optional-donor merge: release status is per published structure and merged by rule."""

from __future__ import annotations

from dataclasses import replace

import pytest

from confflow.execution import confgen_search_worker as worker
from confflow.science.confgen.explore import ExploreSettings
from tests.v4.test_confgen_optional_donor_step import (
    _geom,
    _job,
    _kcal,
    _rec,
    _run_of,
    _toy_graph,
)


def _with_release(run, release: str):  # type: ignore[no-untyped-def]
    return replace(run, stats={**(run.stats or {}), "release": release})


def test_mixed_release_merge_is_deferred_and_per_structure() -> None:
    graph, ref = _toy_graph()
    settings = ExploreSettings(window_kcal=6.0, seed=1)
    run_done = _with_release(
        _run_of([_rec("t00", 0, _kcal(0), {4: True}, _geom(ref, True))]), "done"
    )
    run_deferred = _with_release(
        _run_of([_rec("t00", 0, _kcal(2), {4: False}, _geom(ref, False))]), "deferred"
    )
    job = _job([("s3+", None), ("s3-", None)], settings, graph, ref)
    merged = worker._merge_donor_runs(
        job, [("s3+", settings, run_done), ("s3-", settings, run_deferred)]
    )
    assert len(merged.passing) == 2
    assert merged.stats["release"] == "deferred"
    assert [row["release"] for row in merged.stats["donor_states"]] == ["done", "deferred"]
    # each published structure keeps its owning sub-run's release, in the order it is published
    assert merged.stats["record_release"] == ["done", "deferred"]


@pytest.mark.parametrize(
    ("releases", "expected"),
    [
        (["done", "done"], "done"),
        (["deferred", "done"], "deferred"),
        (["partial", "done"], "partial"),
        (["failed", "done"], "partial"),
        (["failed", "partial"], "partial"),
        (["done", "not_needed"], "done"),
        (["not_needed", "not_needed"], "not_needed"),
        (["off", "off"], "off"),
        (["deferred", "partial", "failed"], "deferred"),
    ],
)
def test_merged_release_rule(releases: list[str], expected: str) -> None:
    assert worker._merged_release(releases) == expected
