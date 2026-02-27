#!/usr/bin/env python3

"""Tests for confflow.core.models — Pydantic data models."""

from __future__ import annotations

import pytest

from confflow.core.models import TaskContext


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
        with pytest.raises(Exception):
            TaskContext(job_name="j")  # type: ignore[call-arg]
