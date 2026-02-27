#!/usr/bin/env python3
from __future__ import annotations

import os
from unittest.mock import patch


def test_rescue_helpers(tmp_path):
    from confflow.calc.scan_ops import (
        _coords_lines_to_xyz,
        _find_failed_ts_input_coords,
        _read_gaussian_input_coords,
        _set_bond_length_on_coords,
        _write_scan_marker,
        _write_ts_failure_report,
        _xyz_to_coords_lines,
    )

    assert _coords_lines_to_xyz(["C 0 0"]) is None
    assert _coords_lines_to_xyz(["C a b c"]) is None

    assert _read_gaussian_input_coords(None) is None
    assert _read_gaussian_input_coords("nonexistent.gjf") is None

    gjf = tmp_path / "test.gjf"
    gjf.write_text("0 1\nC 0 0 0\n\n")
    assert _read_gaussian_input_coords(str(gjf)) == ["C 0 0 0"]

    assert _find_failed_ts_input_coords(str(tmp_path), "job", {"iprog": 1}) is None

    res = _xyz_to_coords_lines([("C", 0, 0, 0)])
    assert "C" in res[0] and "0.000000" in res[0]

    coords = ["C 0 0 0", "H 0 0 1"]
    new_coords = _set_bond_length_on_coords(coords, 1, 2, 1.5)
    assert any("C" in ln and "0.000000" in ln for ln in new_coords)

    _write_ts_failure_report(str(tmp_path), "job", "stage", "message")
    assert (tmp_path / "ts_failures.txt").exists()

    _write_scan_marker(str(tmp_path), "job", "message")
    assert (tmp_path / "job.scan_error.txt").exists()


def test_ts_rescue_scan_no_coords(tmp_path):
    from confflow.calc.rescue import _ts_rescue_scan

    task_info = {
        "job_name": "test",
        "work_dir": str(tmp_path / "work"),
        "config": {"itask": 4, "iprog": 1, "ts_bond_atoms": "1,2"},
        "coords": ["H 0 0 0", "H 0 0 1.0"],
    }

    with patch("confflow.calc.rescue.executor._run_calculation_step") as mock_run:
        mock_run.return_value = {"final_coords": None}
        with patch("confflow.calc.rescue.os.path.exists", return_value=True):
            with patch(
                "confflow.core.io.read_xyz_file",
                return_value=(
                    True,
                    [
                        {
                            "atoms": ["H", "H"],
                            "coords": [[0, 0, 0], [0, 0, 1.0]],
                            "energy": -1.0,
                        }
                    ],
                ),
            ):
                res = _ts_rescue_scan(task_info, "error")
                assert res is None


def test_ts_rescue_scan_drift_failure(tmp_path):
    from confflow.calc.rescue import _ts_rescue_scan

    task_info = {
        "job_name": "test",
        "work_dir": str(tmp_path / "work"),
        "config": {
            "itask": 4,
            "iprog": 1,
            "ts_bond_atoms": "1,2",
            "ts_bond_drift_threshold": 0.1,
        },
        "coords": ["H 0 0 0", "H 0 0 1.0"],
    }

    with patch("confflow.calc.rescue.executor._run_calculation_step") as mock_run:
        mock_run.return_value = {"final_coords": ["H 0 0 0", "H 0 0 1.2"], "e_low": -1.0}
        with patch("confflow.calc.rescue.os.path.exists", return_value=True):
            with patch(
                "confflow.core.io.read_xyz_file",
                return_value=(
                    True,
                    [
                        {
                            "atoms": ["H", "H"],
                            "coords": [[0, 0, 0], [0, 0, 1.2]],
                            "energy": -1.0,
                        }
                    ],
                ),
            ):
                res = _ts_rescue_scan(task_info, "error")
                assert res is None


def test_ts_rescue_scan_rmsd_failure(tmp_path):
    from confflow.calc.rescue import _ts_rescue_scan

    task_info = {
        "job_name": "test",
        "work_dir": str(tmp_path / "work"),
        "config": {"itask": 4, "iprog": 1, "ts_rmsd_threshold": 0.01},
        "coords": ["H 0 0 0", "H 0 0 1.0"],
    }

    with patch("confflow.calc.rescue.executor._run_calculation_step") as mock_run:
        mock_run.return_value = {"final_coords": ["H 0 0 0", "H 0 0 1.1"], "e_low": -1.0}
        with patch("confflow.calc.rescue.os.path.exists", return_value=True):
            with patch(
                "confflow.core.io.read_xyz_file",
                return_value=(
                    True,
                    [
                        {
                            "atoms": ["H", "H"],
                            "coords": [[0, 0, 0], [0, 0, 1.1]],
                            "energy": -1.0,
                        }
                    ],
                ),
            ):
                res = _ts_rescue_scan(task_info, "error")
                assert res is None


def test_ts_rescue_scan_freq_failure(tmp_path):
    from confflow.calc.rescue import _ts_rescue_scan

    task_info = {
        "job_name": "test",
        "work_dir": str(tmp_path / "work"),
        "config": {"itask": 4, "iprog": 1, "keyword": "freq", "ts_bond_atoms": "1,2"},
        "coords": ["H 0 0 0", "H 0 0 1.0"],
    }

    with patch("confflow.calc.rescue.executor._run_calculation_step") as mock_run:
        mock_run.return_value = {
            "final_coords": ["H 0 0 0", "H 0 0 1.0"],
            "e_low": -1.0,
            "num_imag_freqs": 0,
        }
        with patch("confflow.calc.rescue.os.path.exists", return_value=True):
            with patch(
                "confflow.core.io.read_xyz_file",
                return_value=(
                    True,
                    [
                        {
                            "atoms": ["H", "H"],
                            "coords": [[0, 0, 0], [0, 0, 1.0]],
                            "energy": -1.0,
                            "metadata": {"num_imag_freqs": 0},
                        }
                    ],
                ),
            ):
                res = _ts_rescue_scan(task_info, "error")
                assert res is None


def _task_info_for_scan(
    tmp_path, *, keyword: str, config_extra: dict | None = None, job: str = "job"
):
    wd = str(tmp_path)
    os.makedirs(wd, exist_ok=True)
    cfg = {"keyword": keyword, "ts_bond_atoms": "1,2"}
    if config_extra:
        cfg.update(config_extra)

    base_coords = ["C 0 0 0", "H 0 0 1.5"]
    return {"work_dir": wd, "job_name": job, "coords": base_coords, "config": cfg}


def test_ts_rescue_scan_full_logic(tmp_path):
    from confflow.calc.rescue import _ts_rescue_scan

    task_info = _task_info_for_scan(tmp_path / "rescue", keyword="opt ts")

    with (
        patch("confflow.calc.rescue.executor._run_calculation_step") as mock_run,
        patch("confflow.calc.rescue._get_policy"),
    ):
        mock_run.side_effect = [
            {"e_low": -100.0, "final_coords": ["C 0 0 0", "H 0 0 1.5"]},
            {"e_low": -95.0, "final_coords": ["C 0 0 0", "H 0 0 1.4"]},
            {"e_low": -95.0, "final_coords": ["C 0 0 0", "H 0 0 1.6"]},
            {"e_low": -90.0, "final_coords": ["C 0 0 0", "H 0 0 1.7"]},
            {"e_low": -85.0, "final_coords": ["C 0 0 0", "H 0 0 1.8"]},
            {"e_low": -80.0, "final_coords": ["C 0 0 0", "H 0 0 1.9"]},
        ]

        res = _ts_rescue_scan(task_info, fail_reason="test")
        assert res is None


def test_ts_rescue_scan_consecutive_down(tmp_path):
    from confflow.calc.rescue import _ts_rescue_scan

    task_info = _task_info_for_scan(tmp_path / "rescue_down", keyword="opt ts")

    with (
        patch("confflow.calc.rescue.executor._run_calculation_step") as mock_run,
        patch("confflow.calc.rescue._get_policy"),
    ):
        mock_run.side_effect = [
            {"e_low": -100.0, "final_coords": ["C 0 0 0", "H 0 0 1.5"]},
            {"e_low": -105.0, "final_coords": ["C 0 0 0", "H 0 0 1.4"]},
            {"e_low": -110.0, "final_coords": ["C 0 0 0", "H 0 0 1.3"]},
            {"e_low": -95.0, "final_coords": ["C 0 0 0", "H 0 0 1.6"]},
            {"e_low": -105.0, "final_coords": ["C 0 0 0", "H 0 0 1.7"]},
            {"e_low": -110.0, "final_coords": ["C 0 0 0", "H 0 0 1.8"]},
        ]

        res = _ts_rescue_scan(task_info, fail_reason="test")
        assert res is None


def test_ts_rescue_scan_coarse_extend_none(tmp_path):
    from confflow.calc.rescue import _ts_rescue_scan

    task_info = _task_info_for_scan(tmp_path / "rescue_none", keyword="opt ts")

    with patch("confflow.calc.rescue.executor._run_calculation_step") as mock_run:
        mock_run.return_value = {"status": "failed"}
        res = _ts_rescue_scan(task_info, fail_reason="test")
        assert res is None


def test_ts_rescue_scan_error_paths_and_geometric_failure(tmp_path):
    from confflow.calc.rescue import _ts_rescue_scan

    task_info = _task_info_for_scan(
        tmp_path / "rescue_errors",
        keyword="opt ts freq",
        config_extra={"ts_bond_atoms": "1,2"},
    )

    with (
        patch("confflow.calc.rescue.executor._run_calculation_step") as mock_run,
        patch("confflow.calc.rescue._get_policy"),
    ):
        mock_run.side_effect = (
            [
                {"e_low": -100.0, "final_coords": ["C 0 0 0", "H 0 0 1.5"]},
                {"e_low": -110.0, "final_coords": ["C 0 0 0", "H 0 0 1.4"]},
                {"e_low": -90.0, "final_coords": ["C 0 0 0", "H 0 0 1.6"]},
                {"e_low": -110.0, "final_coords": ["C 0 0 0", "H 0 0 1.7"]},
            ]
            + [{"e_low": -95.0, "final_coords": ["C 0 0 0", "H 0 0 1.6"]}] * 11
            + [{"status": "success", "e_low": -90.0, "final_coords": ["C 0 0 0", "H 0 0 1.61"]}]
        )

        res = _ts_rescue_scan(task_info, fail_reason="test")
        assert res is None

    task_info2 = _task_info_for_scan(
        tmp_path / "rescue_geom",
        keyword="opt ts",
        config_extra={"ts_bond_drift_threshold": 0.1},
        job="job2",
    )

    with (
        patch("confflow.calc.rescue.executor._run_calculation_step") as mock_run,
        patch("confflow.calc.rescue._get_policy"),
    ):
        mock_run.side_effect = (
            [
                {"e_low": -100.0, "final_coords": ["C 0 0 0", "H 0 0 1.5"]},
                {"e_low": -110.0, "final_coords": ["C 0 0 0", "H 0 0 1.4"]},
                {"e_low": -90.0, "final_coords": ["C 0 0 0", "H 0 0 1.6"]},
                {"e_low": -110.0, "final_coords": ["C 0 0 0", "H 0 0 1.7"]},
            ]
            + [{"e_low": -95.0, "final_coords": ["C 0 0 0", "H 0 0 1.6"]}] * 11
            + [{"status": "success", "e_low": -90.0, "final_coords": ["C 0 0 0", "H 0 0 1.7"]}]
        )

        res2 = _ts_rescue_scan(task_info2, fail_reason="test")
        assert res2 is None


def test_ts_rescue_scan_internal_patches_v8_style(tmp_path):
    from confflow.calc.rescue import _ts_rescue_scan

    wd = tmp_path / "work"
    wd.mkdir()

    job = "test_job_geom"
    initial_coords = ["3", "comment", "C 0 0 0", "H 0 0 1.1", "H 0 0 -1.1"]

    cfg = {
        "ts_bond_atoms": [2, 3],
        "ts_bond_drift_threshold": 0.1,
        "itask": 3,
        "keyword": "opt(ts,calcfc)",
        "iprog": "gaussian",
    }

    task_info = {
        "work_dir": str(wd),
        "job_name": job,
        "coords": initial_coords,
        "config": cfg,
    }

    def mock_run_step(ts_wd, ts_job, policy, coords, ts_cfg, **kwargs):
        if "scan_0" in ts_job:
            return {"e_low": -1.0, "final_coords": coords}
        if "_rescue" in ts_job and "scan" not in ts_job:
            return {"final_coords": ["2.0"], "e_low": -1.0}
        return {"e_low": -1.1, "final_coords": coords}

    def mock_bond_length(coords, a1, a2):
        try:
            return float(coords[0])
        except Exception:
            return 1.1

    with patch("confflow.calc.rescue.executor._run_calculation_step", side_effect=mock_run_step):
        with (
            patch("confflow.calc.rescue._get_policy"),
            patch("confflow.calc.rescue._keyword_requests_freq", return_value=False),
            patch("confflow.calc.rescue.make_scan_keyword_from_ts_keyword", return_value="scan"),
            patch("confflow.calc.rescue._bond_length_from_xyz_lines", side_effect=mock_bond_length),
            patch("confflow.blocks.refine.rmsd_engine.fast_rmsd", return_value=0.01),
            patch(
                "confflow.calc.scan_ops._set_bond_length_on_coords",
                side_effect=lambda c, a1, a2, r: [str(r)],
            ),
        ):
            res = _ts_rescue_scan(task_info, "fail")
            assert res is None


def test_ts_rescue_scan_freq_failure_with_lowest(tmp_path):
    from confflow.calc.rescue import _ts_rescue_scan

    wd = tmp_path / "work"
    wd.mkdir()

    job = "test_job_freq"
    initial_coords = ["3", "comment", "C 0 0 0", "H 0 0 1.1", "H 0 0 -1.1"]

    cfg = {
        "ts_bond_atoms": [2, 3],
        "itask": 3,
        "keyword": "opt(ts,calcfc) freq",
        "iprog": "gaussian",
    }

    task_info = {"work_dir": str(wd), "job_name": job, "coords": initial_coords, "config": cfg}

    def mock_run_step(ts_wd, ts_job, policy, coords, ts_cfg, **kwargs):
        if "scan_0" in ts_job:
            return {"e_low": -1.0, "final_coords": coords}
        if "_rescue" in ts_job and "scan" not in ts_job:
            return {
                "final_coords": coords,
                "e_low": -1.0,
                "num_imag_freqs": 2,
                "lowest_freq": -100.0,
            }
        return {"e_low": -1.1, "final_coords": coords}

    with patch("confflow.calc.rescue.executor._run_calculation_step", side_effect=mock_run_step):
        with (
            patch("confflow.calc.rescue._get_policy"),
            patch("confflow.calc.rescue._keyword_requests_freq", return_value=True),
            patch("confflow.calc.rescue.make_scan_keyword_from_ts_keyword", return_value="scan"),
            patch("confflow.calc.rescue._bond_length_from_xyz_lines", return_value=1.1),
            patch("confflow.blocks.refine.rmsd_engine.fast_rmsd", return_value=0.01),
            patch(
                "confflow.calc.scan_ops._set_bond_length_on_coords",
                side_effect=lambda c, a1, a2, r: [str(r)],
            ),
        ):
            res = _ts_rescue_scan(task_info, "fail")
            assert res is None
