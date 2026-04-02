#!/usr/bin/env python3

"""Tests for confflow.core.models — Pydantic data models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from confflow.core.models import CalcConfigModel, GlobalConfigModel, TaskContext


class TestTaskContext:
    """Tests for the TaskContext Pydantic model."""

    def test_minimal_creation(self):
        ctx = TaskContext(job_name="j1", work_dir="/tmp", coords=["H 0 0 0"])
        assert ctx.job_name == "j1"
        assert ctx.work_dir == "/tmp"
        assert ctx.coords == ["H 0 0 0"]
        assert ctx.metadata == {}
        assert ctx.config == {}

    def test_full_creation(self):
        ctx = TaskContext(
            job_name="opt1",
            work_dir="/work",
            coords=["C 0 0 0", "H 1 0 0"],
            metadata={"source": "test"},
            config={"charge": 0, "mult": 1},
        )
        assert ctx.metadata["source"] == "test"
        assert ctx.config["charge"] == 0

    def test_extra_fields_allowed(self):
        ctx = TaskContext(
            job_name="j", work_dir="/w", coords=[], custom_field="hello"
        )
        assert ctx.custom_field == "hello"  # type: ignore[attr-defined]

    def test_serialization_roundtrip(self):
        ctx = TaskContext(
            job_name="j1",
            work_dir="/work",
            coords=["H 0 0 0"],
            metadata={"k": "v"},
        )
        data = ctx.model_dump()
        assert isinstance(data, dict)
        assert data["job_name"] == "j1"
        ctx2 = TaskContext(**data)
        assert ctx2 == ctx

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            TaskContext(job_name="j")  # type: ignore[call-arg]


class TestGlobalConfigModel:
    """Tests for the GlobalConfigModel Pydantic model."""

    def test_defaults(self):
        cfg = GlobalConfigModel()
        assert cfg.cores_per_task == 1
        assert cfg.total_memory == "4GB"
        assert cfg.max_parallel_jobs == 1
        assert cfg.charge == 0
        assert cfg.multiplicity == 1
        assert cfg.rmsd_threshold == 0.25
        assert cfg.freeze == []
        assert cfg.ts_bond_atoms is None
        assert cfg.ts_rescue_scan is False
        assert cfg.enable_dynamic_resources is False

    def test_custom_values(self):
        cfg = GlobalConfigModel(
            cores_per_task=8,
            total_memory="64GB",
            max_parallel_jobs=4,
            charge=-1,
            multiplicity=2,
            freeze=[1, 2, 3],
            ts_bond_atoms=[86, 92],
        )
        assert cfg.cores_per_task == 8
        assert cfg.total_memory == "64GB"
        assert cfg.charge == -1
        assert cfg.freeze == [1, 2, 3]
        assert cfg.ts_bond_atoms == [86, 92]

    def test_freeze_from_string(self):
        cfg = GlobalConfigModel(freeze="1,2,3")
        assert cfg.freeze == [1, 2, 3]

    def test_freeze_from_range_string(self):
        cfg = GlobalConfigModel(freeze="1-3,5")
        assert cfg.freeze == [1, 2, 3, 5]

    def test_freeze_from_none(self):
        cfg = GlobalConfigModel(freeze=None)
        assert cfg.freeze == []

    def test_ts_bond_atoms_from_string(self):
        cfg = GlobalConfigModel(ts_bond_atoms="86,92")
        assert cfg.ts_bond_atoms == [86, 92]

    def test_ts_bond_atoms_invalid_string(self):
        cfg = GlobalConfigModel(ts_bond_atoms="1,2,3")
        assert cfg.ts_bond_atoms is None

    def test_cores_validation(self):
        with pytest.raises(Exception, match="cores_per_task"):
            GlobalConfigModel(cores_per_task=0)

    def test_max_jobs_validation(self):
        with pytest.raises(Exception, match="max_parallel_jobs"):
            GlobalConfigModel(max_parallel_jobs=-1)

    def test_multiplicity_validation(self):
        with pytest.raises(Exception, match="multiplicity"):
            GlobalConfigModel(multiplicity=0)

    def test_memory_format_validation(self):
        # Valid formats
        GlobalConfigModel(total_memory="4GB")
        GlobalConfigModel(total_memory="500MB")
        GlobalConfigModel(total_memory="1024KB")

        # Invalid format
        with pytest.raises(Exception, match="total_memory"):
            GlobalConfigModel(total_memory="invalid")

    def test_extra_fields_allowed(self):
        cfg = GlobalConfigModel(custom_param="value")
        assert cfg.custom_param == "value"  # type: ignore[attr-defined]

    def test_serialization(self):
        cfg = GlobalConfigModel(cores_per_task=4, freeze=[1, 2])
        data = cfg.model_dump()
        assert data["cores_per_task"] == 4
        assert data["freeze"] == [1, 2]


class TestCalcConfigModel:
    """Tests for the CalcConfigModel Pydantic model."""

    def test_valid_orca_sp(self):
        cfg = CalcConfigModel(iprog="orca", itask="sp", keyword="HF def2-SVP")
        assert cfg.iprog == "orca"
        assert cfg.itask == "sp"
        assert cfg.keyword == "HF def2-SVP"

    def test_valid_gaussian_ts(self):
        cfg = CalcConfigModel(iprog="g16", itask="ts", keyword="opt=(ts,calcfc) b3lyp/6-31g(d)")
        assert cfg.iprog == "g16"

    def test_valid_numeric_iprog(self):
        cfg = CalcConfigModel(iprog=1, itask=0, keyword="opt b3lyp/6-31g(d)")
        assert cfg.iprog == 1

    def test_invalid_iprog(self):
        with pytest.raises(Exception, match="invalid iprog"):
            CalcConfigModel(iprog="invalid", itask="sp", keyword="HF")

    def test_invalid_itask(self):
        with pytest.raises(Exception, match="invalid itask"):
            CalcConfigModel(iprog="orca", itask="invalid", keyword="HF")

    def test_empty_keyword(self):
        with pytest.raises(Exception, match="keyword"):
            CalcConfigModel(iprog="orca", itask="sp", keyword="")

    def test_whitespace_keyword(self):
        with pytest.raises(Exception, match="keyword"):
            CalcConfigModel(iprog="orca", itask="sp", keyword="   ")

    def test_extra_fields(self):
        cfg = CalcConfigModel(
            iprog="orca", itask="sp", keyword="HF", energy_window=5.0
        )
        assert cfg.energy_window == 5.0  # type: ignore[attr-defined]
