#!/usr/bin/env python3

"""High-CN arrangement diagnostic: CN >= 7 searching across arrangements is reported."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from confflow.domain import FrozenDict
from confflow.domain.diagnostics import DiagnosticSeverity
from confflow.execution import confgen_search_run as search_run
from confflow.execution.confgen_executor import ConfgenExecutor
from confflow.execution.native import NativeExecutionResult
from confflow.execution.work_item_executor import WorkItemExecutor
from tests.v4._dg_search_helpers import _write_fake
from tests.v4.test_confgen_search_step import (
    _SEARCH,
    _SITES,
    _ctx,
    _driving_metal,
    _item,
    _native,
    _run,
    _sci,
)

_CODE = "confgen_high_cn_arrangement_default"


def _work_item() -> SimpleNamespace:
    return SimpleNamespace(id="cg-item:id", logical_key="cg-item")


def _without_arrangement(native: FrozenDict) -> FrozenDict:
    """Drop ``arrangement`` from the search section; the run layer then resolves ``search``."""
    search = {k: v for k, v in native["search"].items() if k != "arrangement"}
    return FrozenDict({**native, "search": search})


def test_cn7_search_warns() -> None:
    warn = search_run._high_cn_arrangement_diagnostic(7, "search", "s", _work_item())
    assert warn is not None and warn.code == _CODE
    assert warn.severity is DiagnosticSeverity.WARNING
    assert "coordination number 7 >= 7" in warn.message
    assert "is 'search' (the default)" in warn.message
    assert "ignore this warning" in warn.message
    assert "set search.arrangement: keep" in warn.message
    assert dict(warn.details) == {"coordination_number": 7, "arrangement": "search"}


def test_cn8_search_warns() -> None:
    warn = search_run._high_cn_arrangement_diagnostic(8, "search", "s", _work_item())
    assert warn is not None and dict(warn.details)["coordination_number"] == 8


@pytest.mark.parametrize("arrangement", ["keep", "enumerate"])
def test_cn7_other_arrangement_is_silent(arrangement: str) -> None:
    assert search_run._high_cn_arrangement_diagnostic(7, arrangement, "s", _work_item()) is None


@pytest.mark.parametrize("cn", [4, 6])
def test_low_cn_is_silent(cn: int) -> None:
    assert search_run._high_cn_arrangement_diagnostic(cn, "search", "s", _work_item()) is None


def test_no_metal_is_silent() -> None:
    assert search_run._high_cn_arrangement_diagnostic(None, "search", "s", _work_item()) is None


def test_wiring_success_path_reports_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completed search with the default arrangement carries the warning (threshold lowered to 4)."""
    monkeypatch.setattr(search_run, "_HIGH_CN_THRESHOLD", 4)
    native = _without_arrangement(
        _native(
            coordination={"metal_center": 1, "binding_sites": _SITES, "shapes": ["square_planar"]},
            search=dict(_SEARCH),
        )
    )
    assert "arrangement" not in native["search"]
    out, _ = _run(native, _driving_metal(), _write_fake(tmp_path), tmp_path)
    assert out.status.value == "completed", [str(d.message) for d in out.diagnostics]
    warnings = [d for d in out.diagnostics if d.code == _CODE]
    assert len(warnings) == 1
    assert dict(warnings[0].details) == {"coordination_number": 4, "arrangement": "search"}


def test_wiring_keep_arrangement_is_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search_run, "_HIGH_CN_THRESHOLD", 4)
    native = _native(
        coordination={"metal_center": 1, "binding_sites": _SITES, "shapes": ["square_planar"]},
        search={**_SEARCH, "arrangement": "keep"},
    )
    out, _ = _run(native, _driving_metal(), _write_fake(tmp_path), tmp_path)
    assert out.status.value == "completed", [str(d.message) for d in out.diagnostics]
    assert not [d for d in out.diagnostics if d.code == _CODE]


def test_wiring_failure_path_reports_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 2 (no passing structures) still reports the high-CN default."""
    monkeypatch.setattr(search_run, "_HIGH_CN_THRESHOLD", 4)
    native = _without_arrangement(
        _native(
            coordination={"metal_center": 1, "binding_sites": _SITES, "shapes": ["square_planar"]},
            search=dict(_SEARCH),
        )
    )
    item = _item("cg-item", [_driving_metal()])
    ctx = _ctx(_sci(native), str(tmp_path), _write_fake(tmp_path))
    rundir = Path(ctx.attempt_dir(item)) / "search_run"

    def _launch(*args: Any, **kwargs: Any) -> Any:
        rundir.mkdir(parents=True, exist_ok=True)
        totals = {"targets": 1, "generated": 2, "relaxed": 2, "passed": 0, "reference_passed": 0}
        search = {
            "relaxations": 2,
            "failures": {"stereo": 2},
            "outcome_class": "chemical_rejection",
        }
        summary = {"targets": [], "structures": [], "fragment_charges": [], "totals": totals,
                   "search": search}  # fmt: skip
        (rundir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        return (NativeExecutionResult(exit_code=2, wall_time_seconds=0.1, timed_out=False), None)

    monkeypatch.setattr(WorkItemExecutor, "launch_and_wait", _launch)
    out = ConfgenExecutor().execute(item, ctx)
    assert out.status.value == "failed"
    assert out.diagnostics[0].code == "confgen_no_passing_structures"
    warnings = [d for d in out.diagnostics if d.code == _CODE]
    assert len(warnings) == 1 and warnings[0].severity.value == "warning"
