#!/usr/bin/env python3

"""Optional-donor merge: release status is per published structure and merged by rule."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from confflow.execution import confgen_search_run as search_run
from confflow.execution import confgen_search_worker as worker
from confflow.science.confgen.explore import ExploreSettings
from tests.v4._dg_search_helpers import _toy, _write_fake
from tests.v4.test_confgen_optional_donor_step import (
    _geom,
    _job,
    _kcal,
    _rec,
    _run_of,
    _toy_graph,
)
from tests.v4.test_confgen_search_step import _driving_metal, _native, _run
from tests.v4.test_confgen_search_worker import _job as _worker_job
from tests.v4.test_confgen_search_worker import _metal_parts, _stub


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
        (["not_needed", "off"], "not_needed"),
        (["off", None], "off"),
    ],
)
def test_merged_release_rule(releases: list[str], expected: str) -> None:
    assert worker._merged_release(releases) == expected


def test_worker_summary_rows_take_record_release_and_warn_on_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    els, ref = _toy()
    _stub(monkeypatch, ref)
    real = worker.explore

    def _merged(*args: Any, **kwargs: Any) -> Any:
        run = real(*args, **kwargs)
        count = len(run.records)
        assert count >= 2
        run.stats["release"] = "done"
        run.stats["record_release"] = ["done", "failed"] + ["done"] * (count - 2)
        return run

    monkeypatch.setattr(worker, "explore", _merged)
    job, _, _, summary_path = _worker_job(tmp_path, *_metal_parts(tmp_path))
    assert worker.main([job]) == 0
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    rows = summary["structures"]
    assert rows[0]["release"] == "done" and rows[1]["release"] == "failed"
    assert all(row["release"] == "done" for row in rows[2:])
    assert "record_release" not in summary["search"]
    assert summary["search"]["release"] == "done"
    err = capsys.readouterr().err
    assert "warning: some release relaxations failed" in err
    assert "every release relaxation failed" not in err
    assert "time budget limited" not in err


def test_release_incomplete_reports_donor_states_and_member_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = search_run._read_validated

    def _mixed(*args: Any) -> Any:
        totals, payload, ordered, frames = real(*args)
        search = payload["search"]
        search["release"] = "done"
        search["donor_states"] = [
            {"label": "s3+", "release": "done"},
            {"label": "s3-", "release": "deferred"},
            "not-a-row",
        ]
        # ordered holds the same row objects as payload["structures"]
        ordered[0]["release"] = "deferred"
        return totals, payload, ordered, frames

    monkeypatch.setattr(search_run, "_read_validated", _mixed)
    out, _ = _run(_native(), _driving_metal(), _write_fake(tmp_path), tmp_path)
    assert out.status.value == "completed", [str(d.message) for d in out.diagnostics]
    incomplete = [d for d in out.diagnostics if d.code == "confgen_release_incomplete"]
    assert len(incomplete) == 1 and incomplete[0].severity.value == "warning"
    details = dict(incomplete[0].details)
    assert details["release"] == "done"
    assert details["release_by_state"] == {"s3+": "done", "s3-": "deferred"}
    assert details["member_release"] == ("deferred", "done")
    members = [dict(m.metadata) for m in out.structures]
    assert members[0]["search_release"] == "deferred"
    assert all(meta["search_release"] == "done" for meta in members[1:])
