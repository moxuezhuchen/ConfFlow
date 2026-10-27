#!/usr/bin/env python3

"""Per-state energy window options: schema bounds, native omission, worker settings mapping."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from confflow.execution import confgen_search_worker as worker
from confflow.workflow.v4.confgen_schema import ConfgenModelV3
from tests.v4.test_confgen_search_worker import _job, _metal_parts

_SEARCH = {"budget": 12, "starts": 2, "embed_timeout_seconds": 20}


def _scope(**search: Any) -> dict[str, Any]:
    return {
        "schema_version": 4,
        "seed": 7,
        "search": {**_SEARCH, **search},
    }


# ----------------------------------------------------------------------------- schema


def test_schema_defaults_and_native_omits_state_keys() -> None:
    model = ConfgenModelV3.model_validate(_scope())
    assert model.search is not None
    assert model.search.state_window_kcal is None
    assert model.search.state_max == 2
    assert model.search.state_max_states == 12
    native = model.scientific_native()["search"]
    assert "state_window_kcal" not in native
    assert "state_max" not in native
    assert "state_max_states" not in native


def test_native_omits_default_state_max_when_window_set() -> None:
    model = ConfgenModelV3.model_validate(_scope(state_window_kcal=12.0))
    native = model.scientific_native()["search"]
    assert native["state_window_kcal"] == 12.0
    assert "state_max" not in native
    assert "state_max_states" not in native


def test_schema_accepts_explicit_state_fields() -> None:
    model = ConfgenModelV3.model_validate(
        _scope(state_window_kcal=8.5, state_max=3, state_max_states=20)
    )
    assert model.search is not None
    assert model.search.state_window_kcal == 8.5
    assert model.search.state_max == 3
    assert model.search.state_max_states == 20
    native = model.scientific_native()["search"]
    assert native["state_max"] == 3
    assert native["state_max_states"] == 20


def test_schema_accepts_window_with_explicit_default_counts() -> None:
    model = ConfgenModelV3.model_validate(_scope(state_window_kcal=6, state_max=2))
    assert model.search is not None
    assert model.search.state_window_kcal == 6.0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state_window_kcal", 0),
        ("state_window_kcal", 0.0),
        ("state_window_kcal", -1.0),
        ("state_window_kcal", "12"),
        ("state_window_kcal", True),
        ("state_max", 0),
        ("state_max", 1.5),
        ("state_max", "2"),
        ("state_max_states", 0),
        ("state_max_states", True),
    ],
)
def test_schema_rejects_out_of_bounds(field: str, value: Any) -> None:
    extra: dict[str, Any] = {"state_window_kcal": 12.0} if field != "state_window_kcal" else {}
    with pytest.raises(ValidationError):
        ConfgenModelV3.model_validate(_scope(**{**extra, field: value}))


@pytest.mark.parametrize(
    "search",
    [
        {"state_max": 3},
        {"state_max_states": 4},
        {"state_max": 2, "state_max_states": 12},
    ],
)
def test_schema_requires_window_for_state_counts(search: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="needs state_window_kcal"):
        ConfgenModelV3.model_validate(_scope(**search))


# ----------------------------------------------------------------------------- worker parsing


def _load_with(tmp_path: Path, **settings: Any) -> dict[str, Any]:
    job, *_ = _job(tmp_path, *_metal_parts(tmp_path), settings_over=settings)
    return worker._load_job(job)


def test_worker_settings_unchanged_when_absent(tmp_path: Path) -> None:
    job = _load_with(tmp_path)
    settings = job["settings"]
    assert getattr(settings, "state_window_kcal", None) is None
    assert getattr(settings, "state_max", 2) == 2
    assert getattr(settings, "state_max_states", 12) == 12


def test_worker_maps_state_fields(tmp_path: Path) -> None:
    job = _load_with(tmp_path, state_window_kcal=9, state_max=3, state_max_states=5)
    settings = job["settings"]
    assert settings.state_window_kcal == 9.0
    assert isinstance(settings.state_window_kcal, float)
    assert settings.state_max == 3
    assert settings.state_max_states == 5


def test_worker_defaults_counts_when_only_window_given(tmp_path: Path) -> None:
    job = _load_with(tmp_path, state_window_kcal=12.0)
    assert job["settings"].state_max == 2
    assert job["settings"].state_max_states == 12


@pytest.mark.parametrize(
    ("settings", "match"),
    [
        ({"state_window_kcal": True}, "state_window_kcal"),
        ({"state_window_kcal": "12"}, "state_window_kcal"),
        ({"state_window_kcal": float("nan")}, "state_window_kcal"),
        ({"state_window_kcal": 0}, "must be > 0"),
        ({"state_window_kcal": -2.0}, "must be > 0"),
        ({"state_window_kcal": 12.0, "state_max": 0}, "settings.state_max"),
        ({"state_window_kcal": 12.0, "state_max_states": 0}, "settings.state_max_states"),
        ({"state_max": 3}, "need 'settings.state_window_kcal'"),
        ({"state_max_states": 4}, "need 'settings.state_window_kcal'"),
    ],
)
def test_worker_rejects_bad_state_values(
    tmp_path: Path, settings: dict[str, Any], match: str
) -> None:
    job, *_ = _job(tmp_path, *_metal_parts(tmp_path), settings_over=settings)
    with pytest.raises(worker._JobError, match=match):
        worker._load_job(job)


def test_worker_rejects_nonfinite_window_via_json_path(tmp_path: Path) -> None:
    job, *_ = _job(tmp_path, *_metal_parts(tmp_path))
    raw = json.loads(Path(job).read_text(encoding="utf-8"))
    raw["settings"]["state_window_kcal"] = "inf"
    Path(job).write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(worker._JobError, match="state_window_kcal"):
        worker._load_job(job)


# ----------------------------------------------------------------------------- comment label


def _record(**over: Any) -> SimpleNamespace:
    base: dict[str, Any] = {"target": "t1", "start": 2, "energy_eh": -10.0, "actual_target": None}
    base.update(over)
    return SimpleNamespace(**base)


def test_comment_kept_state_label() -> None:
    text = worker._comment(_record(kept_by="state", state_flips=(1,)), -10.0)
    assert text.endswith(" flips=1 kept=state")


@pytest.mark.parametrize("kept_by", ["window", None])
def test_comment_other_kept_by_adds_nothing(kept_by: Any) -> None:
    text = worker._comment(_record(kept_by=kept_by, state_flips=(1,)), -10.0)
    assert "kept=" not in text
    assert text.endswith(" flips=1")


def test_comment_missing_kept_by_attribute_adds_nothing() -> None:
    assert "kept=" not in worker._comment(_record(), -10.0)
