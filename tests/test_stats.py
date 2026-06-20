#!/usr/bin/env python3

"""Tests for workflow statistics and tracing helpers."""

from __future__ import annotations

from confflow.workflow.stats import Tracer


def test_trace_low_energy_handles_multiple_final_outputs(tmp_path):
    first_xyz = tmp_path / "first.xyz"
    second_xyz = tmp_path / "second.xyz"
    first_xyz.write_text("1\nCID=A000001 G=-1.0\nH 0 0 0\n", encoding="utf-8")
    second_xyz.write_text("1\nCID=B000001 G=-2.0\nH 0 0 1\n", encoding="utf-8")

    stats = {
        "final_output": None,
        "final_outputs": [str(first_xyz), str(second_xyz)],
        "steps": [
            {
                "index": 1,
                "output_xyz": [str(first_xyz), str(second_xyz)],
            }
        ],
    }

    trace = Tracer.trace_low_energy(stats, k=1)

    assert trace["source_xyz"] is None
    assert trace["source_xyzs"] == [str(first_xyz), str(second_xyz)]
    assert trace["top_k"] == 1
    assert trace["conformers"][0]["cid"] == "B000001"
