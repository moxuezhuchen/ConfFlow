#!/usr/bin/env python3

"""Restraint options: schema bounds, xTB force constant, worker parsing, record comment labels."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from confflow.execution import confgen_search_worker as worker
from confflow.execution.confgen_xtb import XtbCalculator, _constrain_block, _input_blocks
from confflow.science.confgen.calculator import DistanceRestraint
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


def test_schema_new_fields_default_none_and_are_omitted() -> None:
    model = ConfgenModelV3.model_validate(_scope())
    assert model.search is not None
    assert model.search.restraint_force_constant is None
    assert model.search.max_donor_stretch is None
    assert "restraint_force_constant" not in model.scientific_native()["search"]
    assert "max_donor_stretch" not in model.scientific_native()["search"]


def test_schema_accepts_valid_new_fields() -> None:
    model = ConfgenModelV3.model_validate(
        _scope(restraint_force_constant=0.5, max_donor_stretch=1.25)
    )
    assert model.search is not None
    assert model.search.restraint_force_constant == 0.5
    assert model.search.max_donor_stretch == 1.25
    native = model.scientific_native()["search"]
    assert native["restraint_force_constant"] == 0.5
    assert native["max_donor_stretch"] == 1.25


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("restraint_force_constant", 0.0),
        ("restraint_force_constant", -1.0),
        ("max_donor_stretch", 1.0),
        ("max_donor_stretch", 0.5),
        ("max_donor_stretch", "1.2"),
    ],
)
def test_schema_rejects_out_of_bounds(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        ConfgenModelV3.model_validate(_scope(**{field: value}))


# ----------------------------------------------------------------------------- xtb


def test_constrain_block_default_text_unchanged() -> None:
    block = _constrain_block((DistanceRestraint(0, 1, 1.5),))
    assert block == "$constrain\n  force constant=1.0\n  distance: 1, 2, 1.50000\n$end\n"
    assert _input_blocks((DistanceRestraint(0, 1, 1.5),), ()) == block


def test_constrain_block_custom_force_constant() -> None:
    block = _constrain_block((DistanceRestraint(0, 1, 1.5),), 0.25)
    assert "  force constant=0.25\n" in block
    assert _input_blocks((DistanceRestraint(0, 1, 1.5),), (), 0.25) == block


def test_calculator_stores_force_constant_and_threads_it(tmp_path: Path) -> None:
    default = XtbCalculator("xtb", tmp_path, ["H", "H"], 0, 0, 10, 1, base_env={})
    custom = XtbCalculator(
        "xtb", tmp_path, ["H", "H"], 0, 0, 10, 1, restraint_force_constant=2.5, base_env={}
    )
    assert default._force == 1.0
    assert custom._force == 2.5
    restraint = (DistanceRestraint(0, 1, 1.5),)
    assert "force constant=2.5" in _input_blocks(restraint, (), custom._force)


# ----------------------------------------------------------------------------- worker parsing


def _load_with(tmp_path: Path, **settings: Any) -> dict[str, Any]:
    job, *_ = _job(tmp_path, *_metal_parts(tmp_path), settings_over=settings)
    return worker._load_job(job)


def test_worker_defaults_when_absent(tmp_path: Path) -> None:
    job = _load_with(tmp_path)
    assert job["restraint_force_constant"] == 1.0
    assert job["settings"].max_donor_stretch is None


def test_worker_defaults_when_explicit_none(tmp_path: Path) -> None:
    job = _load_with(tmp_path, restraint_force_constant=None, max_donor_stretch=None)
    assert job["restraint_force_constant"] == 1.0
    assert job["settings"].max_donor_stretch is None


def test_worker_parses_valid_values(tmp_path: Path) -> None:
    job = _load_with(tmp_path, restraint_force_constant=3, max_donor_stretch=1.3)
    assert job["restraint_force_constant"] == 3.0
    assert isinstance(job["restraint_force_constant"], float)
    assert job["settings"].max_donor_stretch == 1.3


@pytest.mark.parametrize(
    ("key", "value", "match"),
    [
        ("restraint_force_constant", True, "restraint_force_constant"),
        ("restraint_force_constant", "1.0", "restraint_force_constant"),
        ("restraint_force_constant", float("nan"), "restraint_force_constant"),
        ("restraint_force_constant", float("inf"), "restraint_force_constant"),
        ("restraint_force_constant", 0, "must be > 0"),
        ("restraint_force_constant", -0.5, "must be > 0"),
        ("max_donor_stretch", False, "max_donor_stretch"),
        ("max_donor_stretch", "1.2", "max_donor_stretch"),
        ("max_donor_stretch", float("nan"), "max_donor_stretch"),
        ("max_donor_stretch", 1.0, "must be > 1"),
        ("max_donor_stretch", 0.9, "must be > 1"),
    ],
)
def test_worker_rejects_bad_values(tmp_path: Path, key: str, value: Any, match: str) -> None:
    job, *_ = _job(tmp_path, *_metal_parts(tmp_path), settings_over={key: value})
    with pytest.raises(worker._JobError, match=match):
        worker._load_job(job)


def test_worker_rejects_nonfinite_via_json_path(tmp_path: Path) -> None:
    job, *_ = _job(tmp_path, *_metal_parts(tmp_path))
    raw = json.loads(Path(job).read_text(encoding="utf-8"))
    raw["settings"]["max_donor_stretch"] = "inf"
    Path(job).write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(worker._JobError, match="max_donor_stretch"):
        worker._load_job(job)


def test_finite_float_helper() -> None:
    assert worker._finite_float(2, "x") == 2.0
    with pytest.raises(worker._JobError, match="finite number"):
        worker._finite_float(None, "x")


# ----------------------------------------------------------------------------- comment labels


def _record(**over: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "target": "t1",
        "start": 2,
        "energy_eh": -10.0,
        "actual_target": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


def test_comment_default_record_is_unchanged() -> None:
    text = worker._comment(_record(), -10.0)
    assert text == "target=t1 start=2 energy_eh=-10.000000 rel_kcal=0.000"
    text = worker._comment(_record(actual_target=3, arrangement="a1"), -10.0)
    assert text == ("target=t1 start=2 energy_eh=-10.000000 rel_kcal=0.000 actual=3 arrangement=a1")


def test_comment_new_labels_present() -> None:
    record = _record(
        arrangement="a0",
        reaction_distances=(1.905, 2.31),
        donor_stretch=1.0834,
        state_flips=(1, 3),
    )
    text = worker._comment(record, -10.0)
    assert text.endswith(" arrangement=a0 rxn=1.905,2.310 donor_stretch=1.083 flips=1,3")


def test_comment_empty_flips_and_no_reactions() -> None:
    record = _record(reaction_distances=(), state_flips=())
    text = worker._comment(record, -10.0)
    assert text.endswith(" flips=-")
    assert "rxn=" not in text and "donor_stretch=" not in text


def test_comment_none_fields_add_nothing() -> None:
    record = _record(donor_stretch=None, state_flips=None, reaction_distances=())
    assert worker._comment(record, -10.0) == "target=t1 start=2 energy_eh=-10.000000 rel_kcal=0.000"
