#!/usr/bin/env python3

"""Usage diagnostics of the confgen search: donor stretch, input gap, member metadata."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

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
    _sci,
)

_DONOR = "confgen_donor_stretched"
_GAP = "confgen_input_gap"


def _work_item() -> SimpleNamespace:
    return SimpleNamespace(id="cg-item:id", logical_key="cg-item")


# --- donor stretch --------------------------------------------------------------------------


def _row(ratio: Any, atom: Any = None) -> dict[str, Any]:
    return {"passed": True, "donor_stretch": ratio, "donor_stretch_atom": atom}


@pytest.mark.parametrize("rows", [None, "not-a-list", {}])
def test_donor_non_list_rows_are_silent(rows: Any) -> None:
    assert search_run._donor_stretch_diagnostic(rows, "s", _work_item(), 1) is None


def test_donor_old_rows_without_keys_are_silent() -> None:
    rows = [{"passed": True}, {"passed": True, "donor_stretch": None}]
    assert search_run._donor_stretch_diagnostic(rows, "s", _work_item(), 1) is None


@pytest.mark.parametrize("ratio", [1.25, 1.0, 0.9, True, float("nan"), float("inf"), "1.9"])
def test_donor_at_or_below_threshold_or_invalid_is_silent(ratio: Any) -> None:
    rows = [_row(ratio, 0)]
    assert search_run._donor_stretch_diagnostic(rows, "s", _work_item(), 1) is None


def test_donor_skips_non_mapping_rows() -> None:
    rows = ["junk", None, _row(1.1, 0)]
    assert search_run._donor_stretch_diagnostic(rows, "s", _work_item(), 1) is None


def test_donor_warning_counts_worst_and_maps_atom_to_index_base_one() -> None:
    rows = [_row(1.1, 0), _row(1.4, 2), _row(1.3, 1)]
    warn = search_run._donor_stretch_diagnostic(rows, "s", _work_item(), 1)
    assert warn is not None and warn.code == _DONOR
    assert warn.severity is DiagnosticSeverity.WARNING
    assert warn.step_id == "s"
    assert dict(warn.details) == {"count": 2, "worst": 1.4, "atom": 3, "threshold": 1.25}
    assert "2 published structure(s)" in warn.message
    assert "worst 1.40x, donor atom 3" in warn.message
    assert "a donor moved away from the metal" in warn.message
    assert "check these structures (members carry search_donor_stretch)" in warn.message
    assert "set search.max_donor_stretch to reject them" in warn.message


def test_donor_atom_is_zero_based_with_index_base_zero() -> None:
    rows = [_row(1.6, 4)]
    warn = search_run._donor_stretch_diagnostic(rows, "s", _work_item(), 0)
    assert warn is not None
    assert dict(warn.details)["atom"] == 4
    assert "donor atom 4" in warn.message


def test_donor_unknown_atom_is_reported_as_unknown() -> None:
    for atom in (None, True, "2"):
        warn = search_run._donor_stretch_diagnostic([_row(1.5, atom)], "s", _work_item(), 1)
        assert warn is not None
        assert dict(warn.details)["atom"] is None
        assert "donor atom unknown" in warn.message


# --- input gap ------------------------------------------------------------------------------


@pytest.mark.parametrize("baseline", [None, "x", [], {}, {"lowest_final_delta_kcal": None}])
def test_input_gap_absent_is_silent(baseline: Any) -> None:
    assert search_run._input_gap_diagnostic(baseline, "s", _work_item()) is None


@pytest.mark.parametrize("delta", [float("nan"), float("inf"), True, "1.0"])
def test_input_gap_non_finite_or_non_numeric_is_silent(delta: Any) -> None:
    baseline = {"lowest_final_delta_kcal": delta, "disposition": "kept"}
    assert search_run._input_gap_diagnostic(baseline, "s", _work_item()) is None


def test_input_above_lowest_reports_positive_gap() -> None:
    baseline = {"lowest_final_delta_kcal": -1.5, "disposition": "kept"}
    info = search_run._input_gap_diagnostic(baseline, "s", _work_item())
    assert info is not None and info.code == _GAP
    assert info.severity is DiagnosticSeverity.INFO
    assert dict(info.details) == {"gap_kcal": 1.5, "disposition": "kept"}
    assert info.message == (
        "the input structure is 1.50 kcal/mol above the lowest published structure "
        "(input disposition: kept)"
    )


def test_input_below_lowest_reports_below() -> None:
    baseline = {"lowest_final_delta_kcal": 0.8, "disposition": "published"}
    info = search_run._input_gap_diagnostic(baseline, "s", _work_item())
    assert info is not None
    assert dict(info.details)["gap_kcal"] == pytest.approx(-0.8)
    assert "0.80 kcal/mol below the lowest published structure" in info.message
    assert "input disposition: published" in info.message


def test_input_gap_zero_reads_as_above() -> None:
    baseline = {"lowest_final_delta_kcal": 0.0, "disposition": "kept"}
    info = search_run._input_gap_diagnostic(baseline, "s", _work_item())
    assert info is not None and "0.00 kcal/mol above" in info.message


def test_input_gap_non_string_disposition_is_dropped_from_details() -> None:
    baseline = {"lowest_final_delta_kcal": -2.0, "disposition": None}
    info = search_run._input_gap_diagnostic(baseline, "s", _work_item())
    assert info is not None
    assert dict(info.details)["disposition"] is None
    assert "input disposition: None" in info.message


# --- wiring: success path emits both diagnostics and stores member metadata ----------------


def _write_run_files(rundir: Path, driving: Any) -> None:
    """Write a fake search run: two published structures, the second an old-format row."""
    rundir.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "target": "t0",
            "start": 0,
            "passed": True,
            "energy_eh": -100.0,
            "arrangement": "a0",
            "reaction_distances": [1.9, 2.3],
            "donor_stretch": 1.4,
            "donor_stretch_atom": 2,
            "state_flips": [1, 3],
        },
        {
            "target": "t0",
            "start": 1,
            "passed": True,
            "energy_eh": -99.0,
            "arrangement": "a0",
        },
    ]
    summary = {
        "targets": [],
        "structures": rows,
        "fragment_charges": [],
        "totals": {"targets": 1, "generated": 2, "relaxed": 2, "passed": 2},
        "search": {"relaxations": 2, "failures": {}, "outcome_class": "ok", "release": None},
        "input_baseline": {
            "disposition": "kept",
            "published": True,
            "lowest_final_delta_kcal": -1.5,
        },
    }
    (rundir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    atoms = list(driving.atoms)
    lines: list[str] = []
    for start, row in enumerate(rows):
        lines.append(str(len(atoms)))
        lines.append(f"target={row['target']} start={start} energy_eh={row['energy_eh']}")
        for symbol, (x, y, z) in zip(atoms, driving.coordinates, strict=True):
            lines.append(f"{symbol} {x:.8f} {y:.8f} {z:.8f}")
    (rundir / "structures.xyz").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_wiring_success_path_emits_diagnostics_and_member_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native = _native(
        coordination={"metal_center": 1, "binding_sites": _SITES, "shapes": ["square_planar"]},
        search=dict(_SEARCH),
    )
    driving = _driving_metal()
    item = _item("cg-item", [driving])
    ctx = _ctx(_sci(native), str(tmp_path), _write_fake(tmp_path))
    rundir = Path(ctx.attempt_dir(item)) / "search_run"

    def _launch(*args: Any, **kwargs: Any) -> Any:
        _write_run_files(rundir, driving)
        return (NativeExecutionResult(exit_code=0, wall_time_seconds=0.1, timed_out=False), None)

    monkeypatch.setattr(WorkItemExecutor, "launch_and_wait", _launch)
    out = ConfgenExecutor().execute(item, ctx)
    assert out.status.value == "completed", [str(d.message) for d in out.diagnostics]

    donor = [d for d in out.diagnostics if d.code == _DONOR]
    assert len(donor) == 1 and donor[0].severity is DiagnosticSeverity.WARNING
    assert dict(donor[0].details)["atom"] == 3  # 0-based donor atom 2, index base 1
    gap = [d for d in out.diagnostics if d.code == _GAP]
    assert len(gap) == 1 and gap[0].severity is DiagnosticSeverity.INFO
    assert "1.50 kcal/mol above" in gap[0].message

    first, second = (dict(r.metadata) for r in out.structures)
    assert first["search_reaction_distances"] == (1.9, 2.3)
    assert first["search_donor_stretch"] == 1.4
    assert first["search_state_flips"] == (1, 3)
    assert second["search_reaction_distances"] == ()
    assert second["search_donor_stretch"] is None
    assert second["search_state_flips"] is None
