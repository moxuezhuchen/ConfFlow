#!/usr/bin/env python3

"""Gibbs free-energy provenance across composite SP chains and refine."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from confflow.blocks.refine import processor
from confflow.calc.components.task_runner import TaskRunner
from confflow.calc.result_writer import format_result_comment
from confflow.core.io import parse_comment_metadata


def _run_sp(tmp_path: Path, metadata: dict, *, e_sp: float) -> dict:
    runner = TaskRunner()
    task_info = {
        "job_name": "sp",
        "work_dir": str(tmp_path / "work"),
        "config": {"itask": 1, "iprog": 1},
        "coords": ["C 0 0 0"],
        "metadata": metadata,
    }
    with patch("confflow.calc.components.executor._run_calculation_step") as mock_run:
        mock_run.return_value = {"final_coords": ["C 0 0 0"], "e_low": e_sp}
        with patch("confflow.calc.components.executor.handle_backups"):
            return runner.run(task_info)


def test_freq_then_sp_forms_gibbs_energy(tmp_path):
    freq_result = {"final_gibbs_energy": -100.0, "g_corr": 0.1, "num_imag_freqs": 0}
    meta = parse_comment_metadata(format_result_comment(freq_result, {}))

    res = _run_sp(tmp_path, meta, e_sp=-200.0)

    assert res["status"] == "success"
    assert res["final_sp_energy"] == -200.0
    assert res["final_gibbs_energy"] == -199.9


def test_freq_sp_sp_keeps_gibbs_correction(tmp_path):
    """Chained composite SP must not drop G_corr just because G is present."""
    freq_result = {"final_gibbs_energy": -100.0, "g_corr": 0.1, "num_imag_freqs": 0}
    meta = parse_comment_metadata(format_result_comment(freq_result, {}))

    sp1 = _run_sp(tmp_path, meta, e_sp=-200.0)
    assert sp1["final_gibbs_energy"] == -199.9

    meta2 = parse_comment_metadata(format_result_comment(sp1, {}))
    assert meta2["G_corr"] == 0.1

    sp2 = _run_sp(tmp_path, meta2, e_sp=-300.0)
    assert sp2["final_sp_energy"] == -300.0
    assert sp2["final_gibbs_energy"] == -299.9


def test_freq_sp_refine_sp_keeps_gibbs_correction(tmp_path):
    freq_xyz = tmp_path / "freq.xyz"
    freq_xyz.write_text("1\nRank=1 | G=-100.000000 | G_corr=0.1\nH 0 0 0\n", encoding="utf-8")

    frames = processor.read_xyz_file(str(freq_xyz))
    assert frames[0]["extra_data"]["G_corr"] == 0.1

    refined = tmp_path / "refined.xyz"
    processor._write_refine_output(str(refined), frames, global_min=frames[0]["energy"])
    text = refined.read_text(encoding="utf-8")
    assert "G=-100" in text
    assert "G_corr=0.1" in text

    meta = parse_comment_metadata(text.splitlines()[1])
    assert meta["G_corr"] == 0.1

    res = _run_sp(tmp_path, meta, e_sp=-200.0)
    assert res["final_gibbs_energy"] == -199.9


def test_opt_freq_result_comment_labels_gibbs_and_keeps_g_corr():
    comment = format_result_comment(
        {"final_gibbs_energy": -100.0, "g_corr": 0.1, "num_imag_freqs": 0},
        {},
    )
    assert "G=-100.0" in comment
    assert "Energy=" not in comment
    assert "G_corr=0.1" in comment


def test_plain_energy_comment_stays_energy():
    comment = format_result_comment({"energy": -1.0, "num_imag_freqs": 0}, {})
    assert comment == "Energy=-1.0 Imag=0"
