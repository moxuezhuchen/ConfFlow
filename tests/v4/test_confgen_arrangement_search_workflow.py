#!/usr/bin/env python3

"""Workflow layer of ``arrangement: search``: schema default, worker validation and comment, run metadata.

The core interface (``SearchRecord.arrangement``, ``stats["arrangements"]``) may not exist yet, so
the diagnostic and comment tests use plain dicts and stand-in records. The run tests tag the real
summary in memory after the fake-xTB search has written it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from confflow.domain.diagnostics import DiagnosticSeverity
from confflow.execution import confgen_search_run as search_run
from confflow.execution import confgen_search_worker as worker
from confflow.workflow.v4.confgen_schema import ConfgenModelV3, ConfgenSearchModel
from tests.v4._dg_search_helpers import _write_fake
from tests.v4.test_confgen_search_step import _driving_metal, _native, _run, _summary
from tests.v4.test_confgen_search_worker import _job, _metal_parts

_SITES = [{"id": f"s{i}", "atoms": [d]} for i, d in enumerate((0, 1, 2, 3))]


def _document(shapes: list[str], search: dict[str, Any] | None = None) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": 4,
        "index_base": 0,
        "seed": 1,
        "coordination": {"metal_center": 4, "binding_sites": _SITES, "shapes": shapes},
    }
    if search is not None:
        document["search"] = search
    return document


def _item() -> SimpleNamespace:
    return SimpleNamespace(id="cg-item:id", logical_key="cg-item")


def _arrangements(gap: float | None = 5.7) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {
            "id": "a0",
            "input": True,
            "lowest_eh": -1.0,
            "rel_kcal": gap,
            "minima": 3,
            "published": 1,
        },
        {
            "id": "a1",
            "input": False,
            "lowest_eh": -1.01,
            "rel_kcal": 0.0,
            "minima": 2,
            "published": 2,
        },
    ]
    if gap is None:
        rows = rows[1:]
    return rows


def _rows(arrangement: str | None, passed: bool = True) -> list[dict[str, Any]]:
    row: dict[str, Any] = {"target": "t00", "start": 1, "passed": passed, "energy_eh": -1.01}
    if arrangement is not None:
        row["arrangement"] = arrangement
    return [{"target": "t01", "start": 9, "passed": False}, row]


# --- schema ---------------------------------------------------------------------------------


def test_search_arrangement_defaults_to_search() -> None:
    assert ConfgenSearchModel().arrangement == "search"
    model = ConfgenModelV3.model_validate(_document(["octahedral"], search={"budget": 12}))
    assert model.search is not None and model.search.arrangement == "search"


@pytest.mark.parametrize("value", ["keep", "enumerate", "search"])
def test_search_arrangement_accepts_declared_values(value: str) -> None:
    model = ConfgenModelV3.model_validate(_document(["octahedral"], search={"arrangement": value}))
    assert model.search is not None and model.search.arrangement == value


def test_search_arrangement_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        ConfgenModelV3.model_validate(_document(["octahedral"], search={"arrangement": "auto"}))


def test_input_shape_accepts_search_arrangement() -> None:
    model = ConfgenModelV3.model_validate(_document(["input"], search={"arrangement": "search"}))
    assert model.search is not None and model.search.arrangement == "search"


def test_input_shape_rejects_enumerate_with_search_wording() -> None:
    with pytest.raises(ValidationError) as info:
        ConfgenModelV3.model_validate(_document(["input"], search={"arrangement": "enumerate"}))
    assert "supports arrangement 'keep' or 'search' only" in str(info.value)


def test_input_shape_default_arrangement_is_accepted() -> None:
    model = ConfgenModelV3.model_validate(_document(["input"], search={"budget": 12}))
    assert model.search is not None and model.search.arrangement == "search"


# --- worker ---------------------------------------------------------------------------------


def test_worker_accepts_search_arrangement(tmp_path: Any) -> None:
    job, _, _, _ = _job(tmp_path, *_metal_parts(tmp_path), settings_over={"arrangement": "search"})
    loaded = worker._load_job(job)
    assert loaded["settings"].arrangement == "search"


def test_worker_rejects_unknown_arrangement(
    tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    job, _, _, _ = _job(tmp_path, *_metal_parts(tmp_path), settings_over={"arrangement": "auto"})
    assert worker.main([job]) == 3
    assert "settings.arrangement" in capsys.readouterr().err


def test_comment_carries_arrangement_after_actual_target() -> None:
    record = SimpleNamespace(
        target="t01", start=4, energy_eh=-2.5, actual_target="t02", arrangement="a1"
    )
    text = worker._comment(record, -2.5)  # type: ignore[arg-type]
    assert text == (
        "target=t01 start=4 energy_eh=-2.500000 rel_kcal=0.000 actual=t02 arrangement=a1"
    )


def test_comment_carries_arrangement_without_actual_target() -> None:
    record = SimpleNamespace(
        target="t00", start=2, energy_eh=-2.5, actual_target=None, arrangement="a0"
    )
    text = worker._comment(record, -2.5)  # type: ignore[arg-type]
    assert text.endswith(" arrangement=a0") and "actual=" not in text


def test_comment_omits_arrangement_when_absent() -> None:
    record = SimpleNamespace(target="t00", start=2, energy_eh=-2.5, actual_target=None)
    text = worker._comment(record, -2.5)  # type: ignore[arg-type]
    assert "arrangement=" not in text


def test_comment_keeps_xyz_prefix_for_reader() -> None:
    import re

    record = SimpleNamespace(
        target="t03", start=7, energy_eh=-1.25, actual_target="t01", arrangement="a2"
    )
    text = worker._comment(record, -1.3)  # type: ignore[arg-type]
    match = re.findall(r"target=(\S+) start=(\d+) energy_eh=(\S+)", text)
    assert match == [("t03", "7", "-1.250000")]


# --- diagnostic -----------------------------------------------------------------------------


def test_arrangement_changed_warns_with_input_gap() -> None:
    warn = search_run._arrangement_changed_diagnostic(
        {"arrangements": _arrangements(5.7)}, _rows("a1"), "s", _item()
    )
    assert warn is not None and warn.code == "confgen_arrangement_changed"
    assert warn.severity is DiagnosticSeverity.WARNING
    assert "donor arrangement a1" in warn.message
    assert "5.70 kcal/mol below the input arrangement's lowest (a0)" in warn.message
    assert "search_arrangement" in warn.message
    details = dict(warn.details)
    assert details == {"arrangement": "a1", "input_rel_kcal": 5.7}


def test_arrangement_changed_silent_when_lowest_is_input() -> None:
    assert (
        search_run._arrangement_changed_diagnostic(
            {"arrangements": _arrangements(5.7)}, _rows("a0"), "s", _item()
        )
        is None
    )


def test_arrangement_changed_silent_without_table() -> None:
    assert search_run._arrangement_changed_diagnostic({}, _rows("a1"), "s", _item()) is None
    assert (
        search_run._arrangement_changed_diagnostic(
            {"arrangement_scope": "all_arrangements"}, _rows("a1"), "s", _item()
        )
        is None
    )
    assert search_run._arrangement_changed_diagnostic(None, _rows("a1"), "s", _item()) is None


def test_arrangement_changed_uses_first_passing_row() -> None:
    rows = [
        {"target": "t00", "start": 1, "passed": False, "arrangement": "a1"},
        *_rows("a0"),
    ]
    assert (
        search_run._arrangement_changed_diagnostic(
            {"arrangements": _arrangements()}, rows, "s", _item()
        )
        is None
    )


def test_arrangement_changed_reports_missing_input_row() -> None:
    warn = search_run._arrangement_changed_diagnostic(
        {"arrangements": _arrangements(gap=None)}, _rows("a1"), "s", _item()
    )
    assert warn is not None
    assert "input arrangement not found in the arrangement table" in warn.message
    assert dict(warn.details)["input_rel_kcal"] is None


@pytest.mark.parametrize("gap", ["5.7", float("nan"), True])
def test_arrangement_changed_rejects_non_numeric_gap(gap: Any) -> None:
    rows = _arrangements()
    rows[0]["rel_kcal"] = gap
    warn = search_run._arrangement_changed_diagnostic(
        {"arrangements": rows}, _rows("a1"), "s", _item()
    )
    assert warn is not None and "input arrangement not found" in warn.message


# --- run module -----------------------------------------------------------------------------


def _tag_run(monkeypatch: pytest.MonkeyPatch, arrangement: str, table: Any) -> None:
    """Label every passing record and attach the arrangement table to the real summary."""
    real = search_run._read_validated

    def tagged(*args: Any) -> Any:
        totals, payload, ordered, frames = real(*args)
        for row in payload["structures"]:
            if row.get("passed") is True:
                row["arrangement"] = arrangement
        if table is None:
            payload["search"].pop("arrangements", None)
        else:
            payload["search"]["arrangements"] = table
        return totals, payload, ordered, frames

    monkeypatch.setattr(search_run, "_read_validated", tagged)


def test_run_defaults_arrangement_to_search(tmp_path: Any) -> None:
    out, _ = _run(_native(), _driving_metal(), _write_fake(tmp_path), tmp_path)
    assert out.status.value == "completed", [str(d.message) for d in out.diagnostics]
    assert _summary(out, tmp_path)["settings"]["arrangement"] == "search"


def test_run_sets_search_arrangement_metadata_and_table(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tag_run(monkeypatch, "a1", _arrangements(5.7))
    out, _ = _run(_native(), _driving_metal(), _write_fake(tmp_path), tmp_path)
    assert out.status.value == "completed", [str(d.message) for d in out.diagnostics]
    assert out.structures
    assert {dict(r.metadata)["search_arrangement"] for r in out.structures} == {"a1"}
    info = next(d for d in out.diagnostics if d.code == "confgen_completed")
    assert len(dict(info.details)["arrangements"]) == 2
    warn = [d for d in out.diagnostics if d.code == "confgen_arrangement_changed"]
    assert len(warn) == 1 and "5.70 kcal/mol below" in warn[0].message


def test_run_silent_when_lowest_is_input_arrangement(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tag_run(monkeypatch, "a0", _arrangements(5.7))
    out, _ = _run(_native(), _driving_metal(), _write_fake(tmp_path), tmp_path)
    assert out.status.value == "completed", [str(d.message) for d in out.diagnostics]
    assert {dict(r.metadata)["search_arrangement"] for r in out.structures} == {"a0"}
    assert not [d for d in out.diagnostics if d.code == "confgen_arrangement_changed"]


def test_run_silent_without_arrangement_table(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tag_run(monkeypatch, "a1", None)
    out, _ = _run(_native(), _driving_metal(), _write_fake(tmp_path), tmp_path)
    assert out.status.value == "completed", [str(d.message) for d in out.diagnostics]
    assert not [d for d in out.diagnostics if d.code == "confgen_arrangement_changed"]
    info = next(d for d in out.diagnostics if d.code == "confgen_completed")
    assert dict(info.details)["arrangements"] is None
