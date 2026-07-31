#!/usr/bin/env python3

"""Tests for `workflow.presenter` behavior.

Refactored for clarity: repeated monkeypatch setups are pulled into
module-scoped fixtures so each test focuses on behavior and
assertions rather than setup noise.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from confflow.workflow import presenter


@pytest.fixture(autouse=False)
def viz_stubs(monkeypatch):
    """Provide common viz function stubs used by presenter tests."""
    best_conf = {"metadata": {"CID": "A000001"}, "atoms": ["H"], "coords": [[0.0, 0.0, 0.0]]}
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


def test_print_step_header_block_uses_calc_default_itask(monkeypatch):
    calls = []

    def _mock_step_header(step_idx, total_steps, name, step_type, in_count):
        calls.append(("header", step_idx, total_steps, name, step_type, in_count))

    def _mock_kv(label, value):
        calls.append(("kv", label, value))

    monkeypatch.setattr(presenter, "print_step_header", _mock_step_header)
    monkeypatch.setattr(presenter, "print_kv", _mock_kv)

    presenter.print_step_header_block(
        step_index=1,
        total_steps=1,
        step_name="step_01",
        step_type="calc",
        global_config={},
        params={"iprog": "orca"},
        in_count=1,
    )

    assert "calc (orca/opt_freq)" in calls[0][4]


def test_emit_final_report_and_lowest_updates_stats(viz_stubs, capture_write_xyz, tmp_path):
    input_xyz = tmp_path / "final.xyz"
    input_xyz.write_text("1\n\nH 0 0 0\n", encoding="utf-8")

    final_stats = {}
    logger = MagicMock()

    presenter.emit_final_report_and_lowest(str(input_xyz), [str(input_xyz)], final_stats, logger)

    assert "lowest_conformer" in final_stats
    assert final_stats["lowest_conformer"]["cid"] == "A000001"
    assert final_stats["lowest_conformer"]["energy"] == -1.23
    assert capture_write_xyz["path"].endswith("finalmin.xyz")
    logger.info.assert_called_once()


def test_emit_final_report_and_lowest_handles_multiple_final_outputs(
    viz_stubs, capture_write_xyz, tmp_path
):
    first_xyz = tmp_path / "first.xyz"
    second_xyz = tmp_path / "second.xyz"
    first_xyz.write_text("1\n\nH 0 0 0\n", encoding="utf-8")
    second_xyz.write_text("1\n\nH 0 0 1\n", encoding="utf-8")

    final_stats = {}
    logger = MagicMock()

    presenter.emit_final_report_and_lowest(
        [str(first_xyz), str(second_xyz)],
        [str(first_xyz)],
        final_stats,
        logger,
    )

    assert final_stats["lowest_conformer"]["cid"] == "A000001"
    assert len(final_stats["lowest_conformer"]["source_outputs"]) == 2
    assert capture_write_xyz["path"].endswith("firstmin.xyz")
    logger.info.assert_called_once()


def test_write_final_statistics_outputs_both_json_files(tmp_path):
    final_stats = {
        "input_files": ["a.xyz"],
        "original_input_files": ["a.xyz"],
        "initial_conformers": 2,
        "final_conformers": 1,
        "final_output": str(tmp_path / "final.xyz"),
        "final_outputs": [str(tmp_path / "final.xyz")],
        "total_duration_seconds": 1.23,
        "steps": [
            {
                "index": 1,
                "name": "step_01",
                "type": "calc",
                "status": "completed",
                "input_conformers": 2,
                "output_conformers": 1,
                "failed_conformers": 1,
                "duration_seconds": 1.23,
                "output_xyz": str(tmp_path / "final.xyz"),
            }
        ],
        "lowest_conformer": {"cid": "A000001", "energy": -1.23},
    }

    presenter.write_final_statistics(str(tmp_path), final_stats)

    workflow_stats = tmp_path / "workflow_stats.json"
    run_summary = tmp_path / "run_summary.json"
    output_manifest = tmp_path / "output_manifest.json"
    assert workflow_stats.exists()
    assert run_summary.exists()
    assert output_manifest.exists()

    summary_data = run_summary.read_text(encoding="utf-8")
    assert '"final_conformers": 1' in summary_data
    assert '"completed": 1' in summary_data
    stats_data = json.loads(workflow_stats.read_text(encoding="utf-8"))
    summary_json = json.loads(summary_data)
    assert stats_data["content_schema"] == "confflow.workflow_stats.v1"
    assert summary_json["content_schema"] == "confflow.run_summary.v1"
    manifest = json.loads(output_manifest.read_text(encoding="utf-8"))
    assert manifest == {
        "content_schema": "confflow.output_manifest.v1",
        "terminals": {},
    }


def test_write_final_statistics_makes_terminal_artifacts_relative(tmp_path):
    output = tmp_path / "g16_opt" / "output.xyz"
    output.parent.mkdir()
    output.write_text("1\n\nH 0 0 0\n", encoding="utf-8")

    presenter.write_final_statistics(
        str(tmp_path),
        {"terminal_outputs": {"g16_opt": [str(output)]}},
    )

    manifest = json.loads((tmp_path / "output_manifest.json").read_text(encoding="utf-8"))
    assert manifest == {
        "content_schema": "confflow.output_manifest.v1",
        "terminals": {"g16_opt": ["g16_opt/output.xyz"]},
    }


@pytest.mark.parametrize("artifact", ["../outside.xyz", "nested/../../outside.xyz"])
def test_build_output_manifest_rejects_relative_escape(tmp_path, artifact):
    with pytest.raises(ValueError, match="outside workflow root"):
        presenter.build_output_manifest(
            str(tmp_path),
            {"terminal_outputs": {"terminal": [artifact]}},
        )


def test_build_output_manifest_rejects_absolute_path_outside_root(tmp_path):
    outside = tmp_path.parent / "outside.xyz"

    with pytest.raises(ValueError, match="outside workflow root"):
        presenter.build_output_manifest(
            str(tmp_path),
            {"terminal_outputs": {"terminal": [str(outside)]}},
        )


def test_build_output_manifest_rejects_symlink_escape(tmp_path):
    outside_dir = tmp_path.parent / "outside"
    outside_dir.mkdir(exist_ok=True)
    (outside_dir / "output.xyz").write_text("1\n\nH 0 0 0\n", encoding="utf-8")
    link = tmp_path / "linked"
    try:
        link.symlink_to(outside_dir, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(ValueError, match="outside workflow root"):
        presenter.build_output_manifest(
            str(tmp_path),
            {"terminal_outputs": {"terminal": [str(Path(link) / "output.xyz")]}},
        )
