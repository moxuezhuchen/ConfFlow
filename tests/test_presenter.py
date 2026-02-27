#!/usr/bin/env python3

"""Tests for `workflow.presenter` behavior.

Refactored for clarity: repeated monkeypatch setups are pulled into
module-scoped fixtures so each test focuses on behavior and
assertions rather than setup noise.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from confflow.workflow import presenter


@pytest.fixture(autouse=False)
def viz_stubs(monkeypatch):
    """Provide common viz function stubs used by presenter tests."""
    best_conf = {"metadata": {"CID": "s01_000001"}, "atoms": ["H"], "coords": [[0.0, 0.0, 0.0]]}
    monkeypatch.setattr(presenter.viz, "parse_xyz_file", lambda path: [best_conf])
    monkeypatch.setattr(presenter.viz, "generate_text_report", lambda confs, stats=None: "REPORT")
    monkeypatch.setattr(
        presenter.viz, "get_lowest_energy_conformer", lambda confs: (best_conf, -1.23, 0)
    )
    return best_conf


@pytest.fixture
def capture_write_xyz(monkeypatch):
    written = {}

    def _mock_write_xyz_file(path, conformers, atomic=True):
        written["path"] = path
        written["confs"] = conformers

    monkeypatch.setattr(presenter.io_xyz, "write_xyz_file", _mock_write_xyz_file)
    return written


def test_print_step_header_block_calc(monkeypatch):
    calls = []

    def _mock_step_header(step_idx, total_steps, name, step_type, in_count):
        calls.append(("header", step_idx, total_steps, name, step_type, in_count))

    def _mock_kv(label, value):
        calls.append(("kv", label, value))

    monkeypatch.setattr(presenter, "print_step_header", _mock_step_header)
    monkeypatch.setattr(presenter, "print_kv", _mock_kv)

    presenter.print_step_header_block(
        step_index=1,
        total_steps=3,
        step_name="step_01",
        step_type="calc",
        global_config={"cores_per_task": 8, "total_memory": "64GB", "max_parallel_jobs": 2},
        params={"iprog": "g16", "itask": "opt", "keyword": "b3lyp/6-31g(d)", "freeze": [1, 2]},
        in_count=12,
    )

    assert calls[0][0] == "header"
    assert "calc (g16/opt)" in calls[0][4]
    labels = [x[1] for x in calls if x[0] == "kv"]
    assert labels == ["Keyword", "Resource", "Freeze", "Refine"]


def test_emit_final_report_and_lowest_updates_stats(viz_stubs, capture_write_xyz, tmp_path):
    input_xyz = tmp_path / "final.xyz"
    input_xyz.write_text("1\n\nH 0 0 0\n", encoding="utf-8")

    final_stats = {}
    logger = MagicMock()

    presenter.emit_final_report_and_lowest(str(input_xyz), [str(input_xyz)], final_stats, logger)

    assert "lowest_conformer" in final_stats
    assert final_stats["lowest_conformer"]["cid"] == "s01_000001"
    assert final_stats["lowest_conformer"]["energy"] == -1.23
    assert capture_write_xyz["path"].endswith("finalmin.xyz")
    logger.info.assert_called_once()
